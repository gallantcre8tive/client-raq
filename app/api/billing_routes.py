"""Self-serve trial, Paystack checkout, webhook, registration, pricing admin."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.core.deps import require_platform, require_company
from app.core.security import hash_password, create_access_token
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.billing import Payment, RegistrationToken, PlatformPricing, Subscription
from app.services.billing_service import (
    get_pricing,
    compute_checkout_amount_kobo,
    create_trial_subscription,
    activate_paid_subscription,
    get_company_subscription,
    subscription_is_live,
    new_token,
    new_payment_reference,
    plan_duration_days,
    ensure_billing_tables,
)
from app.services.seed_company import seed_company_from_template
from app.services.email_service import generate_verification_code, send_verification_code, smtp_configured
from app.services.rate_limit import allow as rate_allow
from app.services.audit import write_audit
from app.services.token_crypto import encrypt_secret, decrypt_secret
from app.models.verification import EmailVerification
import json as _json
from app.services.subscription_access import access_snapshot, assert_channel_allowed
from app.services.paystack import (
    initialize_transaction,
    verify_webhook_signature,
    verify_transaction,
    parse_webhook_event,
    paystack_configured,
)

router = APIRouter(tags=["billing"])
settings = get_settings()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "company").lower()).strip("-")
    return (s or "company")[:80]


def _render(request: Request, template: str, ctx: dict | None = None):
    from app.main import render
    return render(request, template, ctx or {})


def _set_company_session(response: RedirectResponse, user_id: int) -> RedirectResponse:
    from app.main import set_session
    return set_session(response, user_id, scope="company")


# ── Public pricing API (landing) ─────────────────────────────────────────────

@router.get("/api/public/pricing")
async def public_pricing(db: AsyncSession = Depends(get_db)):
    pricing = await get_pricing(db)
    return {
        "currency": "NGN",
        "monthly": pricing["monthly_ngn"],
        "six_month": pricing["six_month_ngn"],
        "yearly": pricing["yearly_ngn"],
        "channel_addon_monthly": pricing["channel_addon_monthly_ngn"],
        "guide_fee": pricing["guide_fee_ngn"],
        "trial_hours": pricing["trial_hours"],
        "trial_enabled": pricing["trial_enabled"],
        "six_month_includes_both_channels": pricing["six_month_includes_both_channels"],
        "yearly_includes_both_channels": pricing["yearly_includes_both_channels"],
        "paystack_public_key": getattr(settings, "PAYSTACK_PUBLIC_KEY", "") or "",
        "paystack_ready": paystack_configured(),
    }


# ── Free trial signup ────────────────────────────────────────────────────────

@router.get("/trial", response_class=HTMLResponse)
async def trial_page(request: Request, db: AsyncSession = Depends(get_db)):
    pricing = await get_pricing(db)
    if not pricing.get("trial_enabled"):
        return RedirectResponse("/#pricing", status_code=303)
    return _render(request, "public/trial.html", {
        "trial_hours": pricing["trial_hours"],
        "error": None,
    })


@router.post("/trial")
async def trial_create(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    country: str = Form("Nigeria"),
    business_type: str = Form("printing"),
    phone: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    pricing = await get_pricing(db)
    if not pricing.get("trial_enabled"):
        return _render(request, "public/trial.html", {
            "trial_hours": pricing["trial_hours"],
            "error": "Free trial is currently disabled. Please choose a paid plan.",
        })

    email = (email or "").strip().lower()
    business_name = (business_name or "").strip()
    if len(password or "") < 6:
        return _render(request, "public/trial.html", {
            "trial_hours": pricing["trial_hours"],
            "error": "Password must be at least 6 characters.",
        })

    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if existing:
        return _render(request, "public/trial.html", {
            "trial_hours": pricing["trial_hours"],
            "error": "An account with this email already exists. Please log in.",
        })

    slug = _slugify(business_name)
    base_slug = slug
    n = 1
    while (await db.execute(select(Company).where(Company.slug == slug))).scalars().first():
        n += 1
        slug = f"{base_slug}-{n}"

    currency = "NGN"
    if country and country.lower() not in ("nigeria", "ng"):
        # keep NGN as platform charge currency; company can set display later
        currency = "NGN"

    company = Company(
        name=business_name[:200],
        slug=slug,
        country=country[:100] if country else "Nigeria",
        currency=currency,
        email=email,
        phone=(phone or "")[:30] or None,
        status="active",
        greeting_message=(
            (lambda: (__import__("app.data.business_templates", fromlist=["get_template"]).get_template(business_type or "printing")["default_greeting"].format(name=business_name[:200])))()
        ),
    )
    # optional fields if columns exist
    try:
        company.business_type = (business_type or "printing")[:40]
    except Exception:
        pass

    db.add(company)
    await db.flush()

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=business_name[:200],
        role=UserRole.COMPANY_ADMIN,
        company_id=company.id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(company)
    await db.refresh(user)

    await create_trial_subscription(db, company.id, pricing)
    try:
        await seed_company_from_template(db, company_id=company.id, business_type=business_type or "printing", company_name=business_name)
    except Exception as _se:
        print("seed_trial", _se)

    resp = RedirectResponse("/company/dashboard?trial=1", status_code=303)
    return _set_company_session(resp, user.id)


# ── Checkout (paid) ──────────────────────────────────────────────────────────

@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(
    request: Request,
    plan: str = "monthly",
    db: AsyncSession = Depends(get_db),
):
    if plan not in ("monthly", "six_month", "yearly"):
        plan = "monthly"
    pricing = await get_pricing(db)
    return _render(request, "public/checkout.html", {
        "plan": plan,
        "pricing": pricing,
        "paystack_ready": paystack_configured(),
        "error": None,
    })


@router.post("/checkout/pay")
async def checkout_pay(
    request: Request,
    email: str = Form(...),
    plan_code: str = Form("monthly"),
    channel_whatsapp: Optional[str] = Form("on"),
    channel_telegram: Optional[str] = Form(None),
    include_guide: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    email = (email or "").strip().lower()
    if plan_code not in ("monthly", "six_month", "yearly"):
        plan_code = "monthly"
    want_wa = channel_whatsapp in ("on", "true", "1", "yes")
    want_tg = channel_telegram in ("on", "true", "1", "yes")
    if not want_wa and not want_tg:
        want_wa = True
    guide = include_guide in ("on", "true", "1", "yes")

    pricing = await get_pricing(db)
    # 6m/yearly force both channels available
    if plan_code in ("six_month", "yearly"):
        want_wa = True
        want_tg = True

    amount_kobo, breakdown = compute_checkout_amount_kobo(
        pricing, plan_code,
        want_whatsapp=want_wa, want_telegram=want_tg, include_guide=guide,
    )
    reference = new_payment_reference()
    base = (getattr(settings, "APP_BASE_URL", None) or str(request.base_url)).rstrip("/")
    callback = f"{base}/checkout/callback"

    payment = Payment(
        email=email,
        reference=reference,
        amount_kobo=amount_kobo,
        currency="NGN",
        plan_code=plan_code,
        channel_whatsapp=want_wa,
        channel_telegram=want_tg,
        include_guide=guide,
        status="pending",
    )
    db.add(payment)
    await db.commit()

    if not paystack_configured():
        # Dev fallback: mark success and issue registration token (only if no secret key)
        token = new_token()
        payment.status = "success"
        payment.paid_at = datetime.now(timezone.utc)
        payment.registration_token = token
        rt = RegistrationToken(
            token=token,
            email=email,
            plan_code=plan_code,
            channel_whatsapp=want_wa,
            channel_telegram=want_tg,
            include_guide=guide or plan_code in ("six_month", "yearly"),
            payment_reference=reference,
            amount_kobo=amount_kobo,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        )
        db.add(rt)
        await db.commit()
        return RedirectResponse(f"/register?token={token}", status_code=303)

    result = await initialize_transaction(
        email=email,
        amount_kobo=amount_kobo,
        reference=reference,
        callback_url=callback,
        metadata={
            "plan_code": plan_code,
            "channel_whatsapp": want_wa,
            "channel_telegram": want_tg,
            "include_guide": guide,
            "platform": "client_raq",
        },
    )
    if not result.get("ok"):
        return _render(request, "public/checkout.html", {
            "plan": plan_code,
            "pricing": pricing,
            "paystack_ready": True,
            "error": result.get("error") or "Could not start payment.",
        })
    return RedirectResponse(result["authorization_url"], status_code=303)


@router.get("/checkout/callback")
async def checkout_callback(
    request: Request,
    reference: Optional[str] = None,
    trxref: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    ref = reference or trxref
    if not ref:
        return RedirectResponse("/checkout?error=missing_ref", status_code=303)

    payment = (await db.execute(select(Payment).where(Payment.reference == ref))).scalars().first()
    if not payment:
        return RedirectResponse("/checkout?error=unknown_ref", status_code=303)

    if payment.status == "success" and payment.registration_token:
        return RedirectResponse(f"/register?token={payment.registration_token}", status_code=303)

    verified = await verify_transaction(ref)
    if verified.get("ok") and verified.get("status") == "success":
        token = await _mark_payment_success(db, payment, verified)
        return RedirectResponse(f"/register?token={token}", status_code=303)

    return RedirectResponse("/checkout?error=payment_not_confirmed", status_code=303)


async def _mark_payment_success(db: AsyncSession, payment: Payment, verified: dict | None = None) -> str:
    token = payment.registration_token or new_token()
    payment.status = "success"
    payment.paid_at = datetime.now(timezone.utc)
    payment.registration_token = token
    if verified:
        try:
            payment.paystack_raw = json.dumps(verified.get("raw") or verified)[:8000]
        except Exception:
            pass

    existing = (await db.execute(
        select(RegistrationToken).where(RegistrationToken.token == token)
    )).scalars().first()
    if not existing:
        db.add(RegistrationToken(
            token=token,
            email=payment.email or "",
            plan_code=payment.plan_code or "monthly",
            channel_whatsapp=bool(payment.channel_whatsapp),
            channel_telegram=bool(payment.channel_telegram),
            include_guide=bool(payment.include_guide) or (payment.plan_code or "") in ("six_month", "yearly"),
            payment_reference=payment.reference,
            amount_kobo=payment.amount_kobo or 0,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        ))
    await db.commit()
    return token


# ── Paystack webhook ─────────────────────────────────────────────────────────

@router.post("/webhooks/paystack")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("x-paystack-signature") or request.headers.get("X-Paystack-Signature")

    if paystack_configured() and not verify_webhook_signature(body, signature):
        print("paystack_webhook: invalid signature")
        return JSONResponse({"status": "invalid signature"}, status_code=400)

    event = parse_webhook_event(body)
    event_name = event.get("event") or ""
    data = event.get("data") or {}
    reference = data.get("reference") or ""
    print("paystack_webhook", event_name, reference)

    if event_name == "charge.success" and reference:
        payment = (await db.execute(select(Payment).where(Payment.reference == reference))).scalars().first()
        if payment and payment.status != "success":
            await _mark_payment_success(db, payment, {"raw": event})
            # If payment already linked to company (renewal), activate sub
            if payment.company_id:
                await activate_paid_subscription(
                    db,
                    company_id=payment.company_id,
                    plan_code=payment.plan_code or "monthly",
                    channel_whatsapp=bool(payment.channel_whatsapp),
                    channel_telegram=bool(payment.channel_telegram),
                    include_guide=bool(payment.include_guide),
                )
        elif not payment:
            # create payment row from webhook metadata
            meta = data.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            p = Payment(
                email=(data.get("customer") or {}).get("email"),
                reference=reference,
                amount_kobo=int(data.get("amount") or 0),
                currency=data.get("currency") or "NGN",
                plan_code=meta.get("plan_code") or "monthly",
                channel_whatsapp=bool(meta.get("channel_whatsapp", True)),
                channel_telegram=bool(meta.get("channel_telegram", False)),
                include_guide=bool(meta.get("include_guide", False)),
                status="pending",
            )
            db.add(p)
            await db.commit()
            await db.refresh(p)
            await _mark_payment_success(db, p, {"raw": event})

    return JSONResponse({"status": "ok"})


# ── Register after payment ───────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, token: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    if not token:
        return RedirectResponse("/checkout", status_code=303)
    rt = (await db.execute(select(RegistrationToken).where(RegistrationToken.token == token))).scalars().first()
    if not rt or rt.used:
        return _render(request, "public/register.html", {"error": "This registration link is invalid or already used.", "token": None})
    now = datetime.now(timezone.utc)
    exp = rt.expires_at if rt.expires_at.tzinfo else rt.expires_at.replace(tzinfo=timezone.utc)
    if now > exp:
        return _render(request, "public/register.html", {"error": "This registration link has expired. Please pay again.", "token": None})
    return _render(request, "public/register.html", {
        "error": None,
        "token": token,
        "email": rt.email,
        "plan_code": rt.plan_code,
    })


@router.post("/register")
async def register_submit(
    request: Request,
    token: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    country: str = Form("Nigeria"),
    business_type: str = Form("printing"),
    phone: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    rt = (await db.execute(select(RegistrationToken).where(RegistrationToken.token == token))).scalars().first()
    if not rt or rt.used:
        return _render(request, "public/register.html", {"error": "Invalid or used token.", "token": None})

    email = (email or rt.email or "").strip().lower()
    business_name = (business_name or "").strip()
    if len(password or "") < 6:
        return _render(request, "public/register.html", {
            "error": "Password must be at least 6 characters.",
            "token": token,
            "email": email,
            "plan_code": rt.plan_code,
        })

    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if existing and existing.company_id:
        # renew existing company
        payment = (await db.execute(
            select(Payment).where(Payment.reference == rt.payment_reference)
        )).scalars().first()
        if payment:
            payment.company_id = existing.company_id
        await activate_paid_subscription(
            db,
            company_id=existing.company_id,
            plan_code=rt.plan_code,
            channel_whatsapp=rt.channel_whatsapp,
            channel_telegram=rt.channel_telegram,
            include_guide=rt.include_guide,
        )
        rt.used = True
        await db.commit()
        resp = RedirectResponse("/company/dashboard?subscribed=1", status_code=303)
        return _set_company_session(resp, existing.id)

    if existing:
        return _render(request, "public/register.html", {
            "error": "Email already registered. Log in and renew from Billing.",
            "token": token,
            "email": email,
            "plan_code": rt.plan_code,
        })

    slug = _slugify(business_name)
    base_slug = slug
    n = 1
    while (await db.execute(select(Company).where(Company.slug == slug))).scalars().first():
        n += 1
        slug = f"{base_slug}-{n}"

    company = Company(
        name=business_name[:200],
        slug=slug,
        country=(country or "Nigeria")[:100],
        currency="NGN",
        email=email,
        phone=(phone or "")[:30] or None,
        status="active",
        greeting_message=(
            (lambda: (__import__("app.data.business_templates", fromlist=["get_template"]).get_template(business_type or "printing")["default_greeting"].format(name=business_name[:200])))()
        ),
    )
    try:
        company.business_type = (business_type or "printing")[:40]
    except Exception:
        pass
    db.add(company)
    await db.flush()

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=business_name[:200],
        role=UserRole.COMPANY_ADMIN,
        company_id=company.id,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    await activate_paid_subscription(
        db,
        company_id=company.id,
        plan_code=rt.plan_code,
        channel_whatsapp=rt.channel_whatsapp,
        channel_telegram=rt.channel_telegram,
        include_guide=rt.include_guide,
    )

    payment = (await db.execute(
        select(Payment).where(Payment.reference == rt.payment_reference)
    )).scalars().first()
    if payment:
        payment.company_id = company.id

    rt.used = True
    await db.commit()
    try:
        await seed_company_from_template(db, company_id=company.id, business_type=business_type or "printing", company_name=business_name)
    except Exception as _se:
        print("seed_register", _se)
    await db.refresh(user)

    resp = RedirectResponse("/company/dashboard?subscribed=1", status_code=303)
    return _set_company_session(resp, user.id)


# ── Platform admin: pricing ──────────────────────────────────────────────────

@router.get("/platform/pricing", response_class=HTMLResponse)
async def platform_pricing_page(
    request: Request,
    user: User = Depends(require_platform),
    db: AsyncSession = Depends(get_db),
):
    pricing = await get_pricing(db)
    return _render(request, "platform/pricing.html", {
        "active": "pricing",
        "user_name": user.full_name,
        "pricing": pricing,
        "saved": request.query_params.get("saved"),
    })


@router.post("/platform/pricing")
async def platform_pricing_save(
    user: User = Depends(require_platform),
    db: AsyncSession = Depends(get_db),
    monthly_ngn: float = Form(18000),
    six_month_ngn: float = Form(102000),
    yearly_ngn: float = Form(198000),
    channel_addon_monthly_ngn: float = Form(18000),
    guide_fee_ngn: float = Form(5000),
    trial_hours: int = Form(48),
    trial_enabled: Optional[str] = Form(None),
    trial_message_limit: int = Form(50),
    support_whatsapp: Optional[str] = Form(None),
    support_email: Optional[str] = Form(None),
    social_instagram: Optional[str] = Form(None),
    social_x: Optional[str] = Form(None),
    social_facebook: Optional[str] = Form(None),
    social_tiktok: Optional[str] = Form(None),
    social_linkedin: Optional[str] = Form(None),
    social_youtube: Optional[str] = Form(None),
):
    await ensure_billing_tables(db)
    row = (await db.execute(select(PlatformPricing).where(PlatformPricing.id == 1))).scalars().first()
    if not row:
        row = PlatformPricing(id=1)
        db.add(row)
    row.monthly_ngn = float(monthly_ngn)
    row.six_month_ngn = float(six_month_ngn)
    row.yearly_ngn = float(yearly_ngn)
    row.channel_addon_monthly_ngn = float(channel_addon_monthly_ngn)
    row.guide_fee_ngn = float(guide_fee_ngn)
    row.trial_hours = int(trial_hours)
    row.trial_enabled = trial_enabled in ("on", "true", "1", "yes")
    row.trial_message_limit = int(trial_message_limit)
    row.support_whatsapp = (support_whatsapp or "")[:40] or None
    row.support_email = (support_email or "")[:255] or None
    row.social_instagram = (social_instagram or "")[:200] or None
    row.social_x = (social_x or "")[:200] or None
    row.social_facebook = (social_facebook or "")[:200] or None
    row.social_tiktok = (social_tiktok or "")[:200] or None
    row.social_linkedin = (social_linkedin or "")[:200] or None
    row.social_youtube = (social_youtube or "")[:200] or None
    await db.commit()
    try:
        await write_audit(db, action="platform_pricing_update", detail="prices/socials", actor_user_id=user.id, actor_email=user.email)
    except Exception:
        pass
    return RedirectResponse("/platform/pricing?saved=1", status_code=303)


# ── Company billing + guides ─────────────────────────────────────────────────

@router.get("/company/billing", response_class=HTMLResponse)
async def company_billing(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    pricing = await get_pricing(db)
    sub = await get_company_subscription(db, user.company_id)
    live = subscription_is_live(sub)
    return _render(request, "company/billing.html", {
        "active": "billing",
        "user_name": user.full_name,
        "company_name": getattr(user, "full_name", "Company"),
        "sub": sub,
        "live": live,
        "pricing": pricing,
        "paystack_ready": paystack_configured(),
    })


@router.get("/company/guides", response_class=HTMLResponse)
async def company_guides(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    sub = await get_company_subscription(db, user.company_id)
    pricing = await get_pricing(db)
    unlocked = bool(sub and (sub.guide_unlocked or subscription_is_live(sub)))
    return _render(request, "company/guides.html", {
        "active": "guides",
        "user_name": user.full_name,
        "unlocked": unlocked,
        "sub": sub,
        "support_whatsapp": pricing.get("support_whatsapp") or getattr(settings, "SUPPORT_WHATSAPP", ""),
        "support_email": pricing.get("support_email") or "",
    })


@router.get("/company/onboarding", response_class=HTMLResponse)
async def company_onboarding(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.data.business_templates import get_template, list_types_for_ui
    company = (await db.execute(select(Company).where(Company.id == user.company_id))).scalars().first()
    bt = getattr(company, "business_type", None) or "printing"
    tpl = get_template(bt)
    sub = await get_company_subscription(db, user.company_id)
    return _render(request, "company/onboarding.html", {
        "active": "onboarding",
        "user_name": user.full_name,
        "company": company,
        "template": tpl,
        "business_types": list_types_for_ui(),
        "sub": sub,
        "live": subscription_is_live(sub),
    })



# ── Company renew (logged-in) ────────────────────────────────────────────────

@router.post("/company/billing/renew")
async def company_billing_renew(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
    plan_code: str = Form("monthly"),
    channel_whatsapp: Optional[str] = Form("on"),
    channel_telegram: Optional[str] = Form(None),
    include_guide: Optional[str] = Form(None),
):
    if plan_code not in ("monthly", "six_month", "yearly"):
        plan_code = "monthly"
    want_wa = channel_whatsapp in ("on", "true", "1", "yes")
    want_tg = channel_telegram in ("on", "true", "1", "yes")
    if plan_code in ("six_month", "yearly"):
        want_wa, want_tg = True, True
    if not want_wa and not want_tg:
        want_wa = True
    guide = include_guide in ("on", "true", "1", "yes")

    pricing = await get_pricing(db)
    amount_kobo, breakdown = compute_checkout_amount_kobo(
        pricing, plan_code, want_whatsapp=want_wa, want_telegram=want_tg, include_guide=guide,
    )
    reference = new_payment_reference()
    email = (user.email or "").strip().lower()
    base = (getattr(settings, "APP_BASE_URL", None) or str(request.base_url)).rstrip("/")
    callback = f"{base}/company/billing/callback"

    payment = Payment(
        company_id=user.company_id,
        email=email,
        reference=reference,
        amount_kobo=amount_kobo,
        currency="NGN",
        plan_code=plan_code,
        channel_whatsapp=want_wa,
        channel_telegram=want_tg,
        include_guide=guide,
        status="pending",
    )
    db.add(payment)
    await db.commit()

    if not paystack_configured():
        payment.status = "success"
        payment.paid_at = datetime.now(timezone.utc)
        await db.commit()
        await activate_paid_subscription(
            db,
            company_id=user.company_id,
            plan_code=plan_code,
            channel_whatsapp=want_wa,
            channel_telegram=want_tg,
            include_guide=guide or plan_code in ("six_month", "yearly"),
        )
        return RedirectResponse("/company/billing?renewed=1", status_code=303)

    result = await initialize_transaction(
        email=email,
        amount_kobo=amount_kobo,
        reference=reference,
        callback_url=callback,
        metadata={
            "plan_code": plan_code,
            "channel_whatsapp": want_wa,
            "channel_telegram": want_tg,
            "include_guide": guide,
            "company_id": user.company_id,
            "platform": "client_raq",
        },
    )
    if not result.get("ok"):
        return RedirectResponse("/company/billing?error=pay_init", status_code=303)
    return RedirectResponse(result["authorization_url"], status_code=303)


@router.get("/company/billing/callback")
async def company_billing_callback(
    request: Request,
    reference: Optional[str] = None,
    trxref: Optional[str] = None,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    ref = reference or trxref
    if not ref:
        return RedirectResponse("/company/billing?error=missing_ref", status_code=303)
    payment = (await db.execute(select(Payment).where(Payment.reference == ref))).scalars().first()
    if not payment:
        return RedirectResponse("/company/billing?error=unknown_ref", status_code=303)
    if payment.status != "success":
        verified = await verify_transaction(ref)
        if verified.get("ok") and verified.get("status") == "success":
            payment.status = "success"
            payment.paid_at = datetime.now(timezone.utc)
            payment.company_id = payment.company_id or user.company_id
            await db.commit()
        else:
            return RedirectResponse("/company/billing?error=not_confirmed", status_code=303)
    cid = payment.company_id or user.company_id
    await activate_paid_subscription(
        db,
        company_id=int(cid),
        plan_code=payment.plan_code or "monthly",
        channel_whatsapp=bool(payment.channel_whatsapp),
        channel_telegram=bool(payment.channel_telegram),
        include_guide=bool(payment.include_guide) or (payment.plan_code or "") in ("six_month", "yearly"),
    )
    return RedirectResponse("/company/billing?renewed=1", status_code=303)


@router.get("/company/billing/history", response_class=HTMLResponse)
async def company_billing_history(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Payment).where(Payment.company_id == user.company_id).order_by(Payment.id.desc()).limit(50)
    )).scalars().all()
    return _render(request, "company/billing_history.html", {
        "active": "billing",
        "user_name": user.full_name,
        "payments": rows,
    })


@router.get("/company/telegram", response_class=HTMLResponse)
async def company_telegram_page(
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    company = (await db.execute(select(Company).where(Company.id == user.company_id))).scalars().first()
    ok, msg = await assert_channel_allowed(db, user.company_id, "telegram")
    snap = await access_snapshot(db, user.company_id)
    base = (getattr(settings, "APP_BASE_URL", None) or "https://clientraq.com").rstrip("/")
    webhook_url = f"{base}/webhook/telegram/{company.slug}" if company else ""
    return _render(request, "company/telegram.html", {
        "active": "telegram",
        "user_name": user.full_name,
        "company": company,
        "allowed": ok,
        "allow_msg": msg,
        "snap": snap,
        "webhook_url": webhook_url,
        "saved": request.query_params.get("saved"),
        "error": request.query_params.get("error"),
    })


@router.post("/company/telegram")
async def company_telegram_save(
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
    telegram_bot_token: str = Form(""),
    telegram_enabled: Optional[str] = Form(None),
):
    ok, msg = await assert_channel_allowed(db, user.company_id, "telegram")
    if not ok:
        return RedirectResponse("/company/telegram?error=not_allowed", status_code=303)
    token = (telegram_bot_token or "").strip()
    enabled = telegram_enabled in ("on", "true", "1", "yes")
    stored = encrypt_secret(token) if token else None
    try:
        await db.execute(
            text("UPDATE companies SET telegram_bot_token = :t, telegram_enabled = :e WHERE id = :id"),
            {"t": stored, "e": enabled and bool(token), "id": user.company_id},
        )
        await db.commit()
    except Exception as e:
        print("telegram_save", e)
        try:
            await db.rollback()
        except Exception:
            pass
        return RedirectResponse("/company/telegram?error=save", status_code=303)
    return RedirectResponse("/company/telegram?saved=1", status_code=303)


@router.get("/api/company/access")
async def api_company_access(
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    snap = await access_snapshot(db, user.company_id)
    sub = snap.get("sub")
    return {
        "live": snap["live"],
        "status": snap["status"],
        "hours_left": snap["hours_left"],
        "days_left": snap["days_left"],
        "channel_whatsapp": snap["channel_whatsapp"],
        "channel_telegram": snap["channel_telegram"],
        "guide_unlocked": snap["guide_unlocked"],
        "trial_messages": snap["message_count_trial"],
        "trial_message_limit": snap["trial_message_limit"],
        "plan_code": getattr(sub, "plan_code", None) if sub else None,
        "subscription_ends_at": str(getattr(sub, "subscription_ends_at", None) or ""),
        "trial_ends_at": str(getattr(sub, "trial_ends_at", None) or ""),
    }


# ── Platform ops: payments, suspend, extend ──────────────────────────────────

@router.get("/platform/payments", response_class=HTMLResponse)
async def platform_payments_page(
    request: Request,
    user: User = Depends(require_platform),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Payment).order_by(Payment.id.desc()).limit(100))).scalars().all()
    return _render(request, "platform/payments.html", {
        "active": "payments",
        "user_name": user.full_name,
        "payments": rows,
    })


@router.post("/platform/companies/{company_id}/subscription")
async def platform_company_subscription_action(
    company_id: int,
    user: User = Depends(require_platform),
    db: AsyncSession = Depends(get_db),
    action: str = Form(...),
    days: int = Form(30),
    plan_code: str = Form("monthly"),
):
    from datetime import timedelta
    sub = await get_company_subscription(db, company_id)
    now = datetime.now(timezone.utc)
    if action == "suspend":
        if sub:
            sub.status = "suspended"
            await db.commit()
        else:
            db.add(Subscription(company_id=company_id, status="suspended"))
            await db.commit()
    elif action == "activate":
        if not sub:
            sub = Subscription(company_id=company_id, status="active", plan_code=plan_code,
                               channel_whatsapp=True, channel_telegram=False,
                               subscription_ends_at=now + timedelta(days=int(days or 30)),
                               guide_unlocked=True)
            db.add(sub)
        else:
            sub.status = "active"
            sub.subscription_ends_at = now + timedelta(days=int(days or 30))
            sub.guide_unlocked = True
        await db.commit()
    elif action == "extend_trial":
        hours = int(days or 2) * 24
        if not sub:
            sub = Subscription(company_id=company_id, status="trial", channel_whatsapp=True,
                               trial_ends_at=now + timedelta(hours=hours), guide_unlocked=True)
            db.add(sub)
        else:
            sub.status = "trial"
            base = sub.trial_ends_at or now
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            if base < now:
                base = now
            sub.trial_ends_at = base + timedelta(hours=hours)
        await db.commit()
    elif action == "mark_paid":
        await activate_paid_subscription(
            db, company_id=company_id, plan_code=plan_code or "monthly",
            channel_whatsapp=True, channel_telegram=plan_code in ("six_month", "yearly"),
            include_guide=True,
        )
        ref = new_payment_reference()
        db.add(Payment(
            company_id=company_id, email="manual@platform", reference=ref,
            amount_kobo=0, plan_code=plan_code, status="success",
            channel_whatsapp=True, channel_telegram=plan_code in ("six_month", "yearly"),
            include_guide=True, paid_at=now,
            paystack_raw='{"manual":true}',
        ))
        await db.commit()
    return RedirectResponse(f"/platform/companies?ok={action}", status_code=303)


# ── Legal ────────────────────────────────────────────────────────────────────

@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return _render(request, "public/terms.html", {})


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return _render(request, "public/privacy.html", {})



# ── Email verification for trial signup ──────────────────────────────────────

@router.post("/trial/send-code")
async def trial_send_code(
    request: Request,
    email: str = Form(...),
    business_name: str = Form(...),
    password: str = Form(...),
    country: str = Form("Nigeria"),
    business_type: str = Form("printing"),
    phone: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    honeypot: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Step 1: validate form, email a 6-digit code, stash payload."""
    if honeypot:
        return RedirectResponse("/trial", status_code=303)
    ip = request.client.host if request.client else "unknown"
    if not rate_allow(f"trial_code:{ip}", limit=5, window=600):
        return _render(request, "public/trial.html", {
            "trial_hours": (await get_pricing(db)).get("trial_hours", 48),
            "error": "Too many attempts. Please wait a few minutes.",
            "step": "form",
        })
    email = (email or "").strip().lower()
    business_name = (business_name or "").strip()
    if len(password or "") < 6:
        return _render(request, "public/trial.html", {
            "trial_hours": (await get_pricing(db)).get("trial_hours", 48),
            "error": "Password must be at least 6 characters.",
            "step": "form",
        })
    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if existing:
        return _render(request, "public/trial.html", {
            "trial_hours": (await get_pricing(db)).get("trial_hours", 48),
            "error": "An account with this email already exists. Please log in or subscribe under Billing.",
            "step": "form",
        })

    code = generate_verification_code(6)
    payload = _json.dumps({
        "business_name": business_name,
        "email": email,
        "password": password,
        "country": country,
        "business_type": business_type or "printing",
        "phone": phone or "",
    })
    # invalidate old codes
    try:
        await db.execute(text(
            "UPDATE email_verifications SET used = true WHERE email = :e AND purpose = 'trial' AND used = false"
        ), {"e": email})
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        await ensure_billing_tables(db)
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255),
                    code VARCHAR(12),
                    purpose VARCHAR(40) DEFAULT 'signup',
                    payload_json VARCHAR(4000),
                    attempts INTEGER DEFAULT 0,
                    used BOOLEAN DEFAULT false,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass

    ev = EmailVerification(
        email=email,
        code=code,
        purpose="trial",
        payload_json=payload[:4000],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(ev)
    await db.commit()

    result = send_verification_code(email, code, purpose="trial")
    tip = None
    if result.get("dev_fallback"):
        tip = "Email SMTP is not configured yet — check Render logs for EMAIL_DEV_FALLBACK and the code."
    return _render(request, "public/trial_verify.html", {
        "email": email,
        "error": None,
        "tip": tip,
        "smtp_ok": smtp_configured(),
    })


@router.post("/trial/verify")
async def trial_verify_code(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email = (email or "").strip().lower()
    code = (code or "").strip()
    ip = request.client.host if request.client else "unknown"
    if not rate_allow(f"trial_verify:{ip}", limit=20, window=600):
        return _render(request, "public/trial_verify.html", {
            "email": email, "error": "Too many attempts. Try again later.", "tip": None, "smtp_ok": smtp_configured(),
        })

    row = (await db.execute(
        select(EmailVerification)
        .where(
            EmailVerification.email == email,
            EmailVerification.purpose == "trial",
            EmailVerification.used == False,  # noqa: E712
        )
        .order_by(EmailVerification.id.desc())
    )).scalars().first()

    if not row:
        return _render(request, "public/trial_verify.html", {
            "email": email, "error": "No active code. Start again from the trial form.", "tip": None, "smtp_ok": smtp_configured(),
        })

    now = datetime.now(timezone.utc)
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if now > exp:
        return _render(request, "public/trial_verify.html", {
            "email": email, "error": "Code expired. Request a new one.", "tip": None, "smtp_ok": smtp_configured(),
        })

    row.attempts = int(row.attempts or 0) + 1
    if row.attempts > 8:
        row.used = True
        await db.commit()
        return _render(request, "public/trial_verify.html", {
            "email": email, "error": "Too many wrong attempts. Start again.", "tip": None, "smtp_ok": smtp_configured(),
        })

    if (row.code or "") != code:
        await db.commit()
        return _render(request, "public/trial_verify.html", {
            "email": email, "error": "Incorrect code. Check your email and try again.", "tip": None, "smtp_ok": smtp_configured(),
        })

    # success — create account from payload
    try:
        payload = _json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}
    business_name = (payload.get("business_name") or "My Business").strip()
    password = payload.get("password") or ""
    country = payload.get("country") or "Nigeria"
    business_type = payload.get("business_type") or "printing"
    phone = payload.get("phone") or None

    existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
    if existing:
        row.used = True
        await db.commit()
        return RedirectResponse("/company/login?verified=1", status_code=303)

    pricing = await get_pricing(db)
    slug = _slugify(business_name)
    base_slug = slug
    n = 1
    while (await db.execute(select(Company).where(Company.slug == slug))).scalars().first():
        n += 1
        slug = f"{base_slug}-{n}"

    from app.data.business_templates import get_template
    tpl = get_template(business_type)
    greeting = tpl["default_greeting"].format(name=business_name[:200])

    company = Company(
        name=business_name[:200],
        slug=slug,
        country=(country or "Nigeria")[:100],
        currency="NGN",
        email=email,
        phone=(phone or "")[:30] or None,
        status="active",
        greeting_message=greeting,
    )
    try:
        company.business_type = (business_type or "printing")[:40]
    except Exception:
        pass
    db.add(company)
    await db.flush()

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=business_name[:200],
        role=UserRole.COMPANY_ADMIN,
        company_id=company.id,
        is_active=True,
    )
    db.add(user)
    row.used = True
    await db.commit()
    await db.refresh(company)
    await db.refresh(user)

    await create_trial_subscription(db, company.id, pricing)
    try:
        await seed_company_from_template(db, company_id=company.id, business_type=business_type, company_name=business_name)
    except Exception as _se:
        print("seed_trial_verify", _se)
    try:
        await write_audit(db, action="trial_signup_verified", detail=email, actor_email=email, company_id=company.id, ip=ip)
    except Exception:
        pass

    resp = RedirectResponse("/company/onboarding?trial=1", status_code=303)
    return _set_company_session(resp, user.id)
