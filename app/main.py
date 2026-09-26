"""Client-RaQ full-stack: frontend UI + multi-tenant backend."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from typing import Optional
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.config import get_settings
from app.database import get_db, init_db
from app.models.user import User, UserRole
from app.models.company import Company, CompanyStatus, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service
from app.models.conversation import Order, Conversation, OrderStatus
from app.core.security import hash_password, verify_password, create_access_token
from app.core.deps import get_current_user, require_platform, require_company

settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title="Client-RaQ")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
media_path = BASE.parent / "media"
media_path.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)


def set_session(response: RedirectResponse, user_id: int) -> RedirectResponse:
    token = create_access_token({"sub": str(user_id)})
    response.set_cookie(
        settings.SESSION_COOKIE, token,
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
    )
    return response


def clear_session(response: RedirectResponse) -> RedirectResponse:
    response.delete_cookie(settings.SESSION_COOKIE)
    return response


@app.on_event("startup")
async def startup():
    try:
        await init_db()
    except Exception as e:
        print(f"DB init warning: {e}")


# ---------- Public ----------

from datetime import datetime, timedelta, timezone

async def revenue_stats(db: AsyncSession, company_id: int | None = None) -> dict:
    zero = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    try:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = day_start.replace(day=1)

        async def sum_since(since):
            try:
                q = select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.created_at >= since)
                if company_id is not None:
                    q = q.where(Order.company_id == company_id)
                try:
                    q = q.where(Order.status.in_([
                        OrderStatus.PAYMENT_CONFIRMED,
                        OrderStatus.IN_PRODUCTION,
                        OrderStatus.READY,
                        OrderStatus.COMPLETED,
                    ]))
                except Exception:
                    pass
                return float((await db.execute(q)).scalar() or 0)
            except Exception as e:
                print("revenue sum_since:", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
                return 0.0

        return {
            "daily": await sum_since(day_start),
            "weekly": await sum_since(week_start),
            "monthly": await sum_since(month_start),
        }
    except Exception as e:
        print("revenue_stats:", e)
        try:
            await db.rollback()
        except Exception:
            pass
        return zero


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return render(request, "public/landing.html")


# ---------- Platform auth ----------
@app.get("/platform/login", response_class=HTMLResponse)
async def platform_login_page(request: Request):
    return render(request, "auth/platform_login.html", {"error": None})


@app.post("/platform/login")
async def platform_login(
    request: Request, email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(User).where(User.email == email.strip().lower()))
    user = r.scalar_one_or_none()
    if not user or user.role != UserRole.PLATFORM_ADMIN or not verify_password(password, user.hashed_password):
        return render(request, "auth/platform_login.html", {"error": "Invalid email or password"}, 400)
    resp = RedirectResponse("/platform/dashboard", status_code=303)
    return set_session(resp, user.id)


@app.get("/platform/logout")
async def platform_logout():
    return clear_session(RedirectResponse("/platform/login", status_code=303))


# ---------- Platform pages ----------
@app.get("/platform/dashboard", response_class=HTMLResponse)
async def platform_dashboard(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies, company_rows = [], []
    cc = oc = vc = 0
    rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    try:
        companies = (await db.execute(select(Company).order_by(Company.created_at.desc()).limit(20))).scalars().all()
        cc = (await db.execute(select(func.count()).select_from(Company))).scalar() or 0
    except Exception as e:
        print("dash companies:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        oc = (await db.execute(select(func.count()).select_from(Order))).scalar() or 0
    except Exception as e:
        print("dash orders:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        vc = (await db.execute(select(func.count()).select_from(Conversation))).scalar() or 0
    except Exception as e:
        print("dash conv:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        rev = await revenue_stats(db)
    except Exception as e:
        print("dash rev:", e)
        try: await db.rollback()
        except Exception: pass
        rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    for c in companies:
        st = c.status if isinstance(c.status, str) else getattr(c.status, "value", str(c.status))
        company_rows.append({
            "id": c.id, "name": c.name, "slug": c.slug,
            "country": c.country or "—", "currency": c.currency or "NGN",
            "status": st,
            "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "—",
        })
    return render(request, "platform/dashboard.html", {
        "active": "dashboard",
        "user_name": user.full_name or "Platform Admin",
        "companies_count": cc, "orders_count": oc, "conv_count": vc,
        "revenue": f'{rev["monthly"]:,.0f}',
        "revenue_daily": rev["daily"], "revenue_weekly": rev["weekly"], "revenue_monthly": rev["monthly"],
        "companies": company_rows,
    })


@app.get("/platform/companies", response_class=HTMLResponse)
async def platform_companies(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies = (await db.execute(select(Company).order_by(Company.created_at.desc()))).scalars().all()
    rows = [
        {"id": c.id, "name": c.name, "slug": c.slug, "country": c.country, "currency": c.currency,
         "status": (c.status if isinstance(c.status, str) else getattr(c.status, "value", str(c.status))), "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "—"}
        for c in companies
    ]
    return render(request, "platform/companies.html", {
        "active": "companies", "user_name": user.full_name, "companies": rows,
    })


@app.get("/platform/companies/new", response_class=HTMLResponse)
async def add_company_page(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/provision.html", {
        "active": "add", "user_name": user.full_name, "error": None,
    })


@app.post("/platform/companies/new")
async def add_company_submit(
    request: Request,
    name: str = Form(...), slug: str = Form(...), country: str = Form("Nigeria"),
    currency: str = Form("NGN"), phone: Optional[str] = Form(None),
    admin_full_name: str = Form(...), admin_email: str = Form(...), admin_password: str = Form(...),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    slug_c = slug.strip().lower().replace(" ", "-")
    admin_email_c = admin_email.strip().lower()
    name_c = name.strip()
    phone_c = (phone or "").strip() or None
    greet = f"Welcome to {name_c}! How can we help with your printing order today?"
    try:
        if (await db.execute(select(Company).where(Company.slug == slug_c))).scalar_one_or_none():
            return render(request, "platform/provision.html", {
                "active": "add", "user_name": user.full_name or "Admin",
                "error": "Slug already exists. Choose another.",
            }, 400)
        if (await db.execute(select(User).where(User.email == admin_email_c))).scalar_one_or_none():
            return render(request, "platform/provision.html", {
                "active": "add", "user_name": user.full_name or "Admin",
                "error": "That admin email is already registered.",
            }, 400)
        row = (await db.execute(sa_text("""
            INSERT INTO companies (name, slug, country, currency, phone, greeting_message, bot_language, status)
            VALUES (:name, :slug, :country, :currency, :phone, :greet, 'both', 'active')
            RETURNING id
        """), {
            "name": name_c, "slug": slug_c,
            "country": country or "Nigeria", "currency": currency or "NGN",
            "phone": phone_c, "greet": greet,
        })).first()
        company_id = int(row[0])
        db.add(User(
            email=admin_email_c,
            hashed_password=hash_password(admin_password),
            full_name=admin_full_name.strip(),
            role=UserRole.COMPANY_ADMIN,
            company_id=company_id,
            is_active=True,
        ))
        await db.commit()
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        print("add_company ERROR:", repr(e))
        import traceback
        traceback.print_exc()
        return render(request, "platform/provision.html", {
            "active": "add", "user_name": getattr(user, "full_name", None) or "Admin",
            "error": f"Could not create company: {e}",
        }, 400)
    base = str(request.base_url).rstrip("/")
    return render(request, "platform/company_created.html", {
        "active": "companies", "user_name": getattr(user, "full_name", None) or "Admin",
        "company_name": name_c, "company_slug": slug_c,
        "login_url": f"{base}/company/login",
        "admin_email": admin_email_c, "admin_password": admin_password,
    })


@app.post("/platform/companies/{company_id}/suspend")
async def suspend_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        c.status = "suspended"
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.post("/platform/companies/{company_id}/activate")
async def activate_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        c.status = "active"
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.post("/platform/companies/{company_id}/delete")
async def delete_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        await db.delete(c)
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.get("/platform/companies/{company_id}/admins", response_class=HTMLResponse)
async def company_admins_page(
    company_id: int, request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(404)
    admins = (await db.execute(select(User).where(User.company_id == company_id))).scalars().all()
    return render(request, "admin_company_admins.html", {
        "active": "companies", "user_name": user.full_name,
        "company": company, "admins": admins, "error": None, "created": None,
    })


@app.get("/platform/messages", response_class=HTMLResponse)
async def platform_messages(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies = (await db.execute(select(Company).order_by(Company.name))).scalars().all()
    notes = [{"company": c.name, "message": c.platform_note} for c in companies if c.platform_note]
    return render(request, "platform/messages.html", {
        "active": "messages", "user_name": user.full_name,
        "companies": companies, "notes": notes,
    })


@app.post("/platform/messages")
async def platform_messages_send(
    company_id: int = Form(...), message: str = Form(...),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    c = await db.get(Company, company_id)
    if c:
        c.platform_note = message.strip()
        await db.commit()
    return RedirectResponse("/platform/messages", status_code=303)


@app.get("/platform/infrastructure", response_class=HTMLResponse)
async def platform_infra(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/infrastructure.html", {"active": "infra", "user_name": user.full_name})


@app.get("/platform/settings", response_class=HTMLResponse)
async def platform_settings(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "success": None, "support_whatsapp": settings.SUPPORT_WHATSAPP,
    })


@app.post("/platform/settings")
async def platform_settings_save(
    request: Request, full_name: str = Form(...), email: str = Form(...),
    current_password: Optional[str] = Form(None), new_password: Optional[str] = Form(None),
    confirm_password: Optional[str] = Form(None),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    user.full_name = full_name.strip()
    user.email = email.strip().lower()
    if new_password:
        if new_password != (confirm_password or ""):
            return render(request, "platform/settings.html", {
                "active": "settings", "user_name": user.full_name, "user_email": user.email,
                "success": None, "support_whatsapp": settings.SUPPORT_WHATSAPP,
            })
        if current_password and verify_password(current_password, user.hashed_password):
            user.hashed_password = hash_password(new_password)
    await db.commit()
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "success": "Profile updated.", "support_whatsapp": settings.SUPPORT_WHATSAPP,
    })


@app.post("/platform/settings/secrets")
async def platform_secrets_save(
    request: Request,
    support_whatsapp: Optional[str] = Form(None),
    database_url: Optional[str] = Form(None),
    secret_key: Optional[str] = Form(None),
    wa_verify_token: Optional[str] = Form(None),
    llm_api_key: Optional[str] = Form(None),
    user: User = Depends(require_platform),
):
    # Frontend phase stores note; production should write encrypted secrets store / .env securely
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "support_whatsapp": support_whatsapp or settings.SUPPORT_WHATSAPP,
        "success": "Secrets received. Wire encrypted persistence in deployment.",
    })


# ---------- Company auth ----------
@app.get("/company/login", response_class=HTMLResponse)
async def company_login_page(request: Request):
    return render(request, "auth/company_login.html", {"error": None, "brand": ""})


@app.post("/company/login")
async def company_login(
    request: Request, brand: str = Form(...), email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    brand_q = brand.strip().lower()
    company = (await db.execute(
        select(Company).where(or_(
            func.lower(Company.name) == brand_q,
            Company.slug == brand_q.replace(" ", "-"),
        ))
    )).scalar_one_or_none()
    if not company or company.status == "suspended":
        return render(request, "auth/company_login.html", {
            "error": "Company not found or suspended", "brand": brand,
        }, 400)
    user = (await db.execute(
        select(User).where(User.email == email.strip().lower(), User.company_id == company.id)
    )).scalar_one_or_none()
    if not user or user.role not in (UserRole.COMPANY_ADMIN, UserRole.COMPANY_STAFF):
        return render(request, "auth/company_login.html", {
            "error": "Invalid email or password for this company", "brand": brand,
        }, 400)
    if not verify_password(password, user.hashed_password):
        return render(request, "auth/company_login.html", {
            "error": "Invalid email or password for this company", "brand": brand,
        }, 400)
    resp = RedirectResponse("/company/dashboard", status_code=303)
    return set_session(resp, user.id)


@app.get("/company/logout")
async def company_logout():
    return clear_session(RedirectResponse("/company/login", status_code=303))


# ---------- Company pages ----------
async def company_ctx(user: User, db: AsyncSession):
    company = await db.get(Company, user.company_id)
    return company


@app.get("/company/dashboard", response_class=HTMLResponse)
async def company_dashboard(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    oc = (await db.execute(select(func.count()).select_from(Order).where(Order.company_id == company.id))).scalar() or 0
    pending = (await db.execute(select(func.count()).select_from(Order).where(
        Order.company_id == company.id, Order.status == OrderStatus.PAYMENT_SUBMITTED
    ))).scalar() or 0
    chats = (await db.execute(select(func.count()).select_from(Conversation).where(
        Conversation.company_id == company.id
    ))).scalar() or 0
    recent = (await db.execute(
        select(Order).where(Order.company_id == company.id).order_by(Order.created_at.desc()).limit(10)
    )).scalars().all()
    recent_orders = [
        {"id": o.id, "customer": o.customer_name or o.customer_wa_id, "service": o.service_name or "—",
         "status": (o.status.value if hasattr(o.status, "value") else str(o.status)), "total": f"{o.currency} {o.total_amount:,.0f}"}
        for o in recent
    ]
    return render(request, "company/dashboard.html", {
        "active": "dashboard", "company_name": company.name, "user_name": user.full_name,
        "orders_count": oc, "pending_count": pending, "chats_count": chats,
        "recent_orders": recent_orders, "platform_note": company.platform_note,
    })


@app.get("/company/services", response_class=HTMLResponse)
async def company_services(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    services = (await db.execute(select(Service).where(Service.company_id == company.id))).scalars().all()
    rows = [{"name": s.name, "category": s.category, "price": f"{company.currency} {s.base_price:,.0f} / {s.unit}"} for s in services]
    return render(request, "company/services.html", {
        "active": "services", "company_name": company.name, "user_name": user.full_name, "services": rows,
    })


@app.post("/company/services/add")
async def company_services_add(
    request: Request,
    category: str = Form(...), name: str = Form(...), description: Optional[str] = Form(None),
    base_price: float = Form(0), unit: str = Form("per piece"),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    db.add(Service(
        company_id=user.company_id, category=category, name=name.strip(),
        description=description, base_price=base_price, unit=unit, is_active=True,
    ))
    await db.commit()
    return RedirectResponse("/company/services", status_code=303)


@app.get("/company/orders", response_class=HTMLResponse)
async def company_orders(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    orders = (await db.execute(
        select(Order).where(Order.company_id == company.id).order_by(Order.created_at.desc())
    )).scalars().all()
    rows = [
        {"id": o.id, "customer": o.customer_name or o.customer_wa_id, "service": o.service_name or "—",
         "status": (o.status.value if hasattr(o.status, "value") else str(o.status)).replace("_", " "), "status_class": "pending" if "await" in (o.status.value if hasattr(o.status, "value") else str(o.status)) or "payment" in (o.status.value if hasattr(o.status, "value") else str(o.status)) else "active",
         "total": f"{o.currency} {o.total_amount:,.0f}"}
        for o in orders
    ]
    return render(request, "company/orders.html", {
        "active": "orders", "company_name": company.name, "user_name": user.full_name, "orders": rows,
    })


@app.get("/company/orders/{order_id}", response_class=HTMLResponse)
async def company_order_detail(
    order_id: int, request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    order = await db.get(Order, order_id)
    if not order or order.company_id != company.id:
        raise HTTPException(404)
    return render(request, "company/order_detail.html", {
        "active": "orders", "company_name": company.name, "user_name": user.full_name,
        "order_id": order.id, "customer": order.customer_name or order.customer_wa_id,
        "service": order.service_name, "total": f"{order.currency} {order.total_amount:,.0f}",
        "status": order.status.value, "payment_proof": order.payment_proof_url,
    })


@app.post("/company/orders/{order_id}/status")
async def company_order_status(
    order_id: int, status: str = Form(...), note: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, order_id)
    if not order or order.company_id != user.company_id:
        raise HTTPException(404)
    try:
        order.status = OrderStatus(status)
    except ValueError:
        pass
    order.status_note = note
    await db.commit()
    try:
        from app.services.bot_engine import notify_order_status
        await notify_order_status(db, order, status, note)
    except Exception:
        pass
    return RedirectResponse(f"/company/orders/{order_id}", status_code=303)


@app.get("/company/payments", response_class=HTMLResponse)
async def company_payments(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    accounts = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == company.id))).scalars().all()
    return render(request, "company/payments.html", {
        "active": "payments", "company_name": company.name, "user_name": user.full_name, "accounts": accounts,
    })


@app.post("/company/payments/add")
async def company_payments_add(
    bank_name: str = Form(...), account_name: str = Form(...), account_number: str = Form(...),
    instructions: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    existing = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == user.company_id))).scalars().first()
    db.add(PaymentDetail(
        company_id=user.company_id, bank_name=bank_name, account_name=account_name,
        account_number=account_number, instructions=instructions, is_primary=existing is None,
    ))
    await db.commit()
    return RedirectResponse("/company/payments", status_code=303)


@app.get("/company/chats", response_class=HTMLResponse)
async def company_chats(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    company = await company_ctx(user, db)
    return render(request, "company/chats.html", {
        "active": "chats", "company_name": company.name, "user_name": user.full_name,
    })


@app.get("/company/bot-settings", response_class=HTMLResponse)
async def company_bot(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    import json
    company = await company_ctx(user, db)
    flags = {}
    try:
        flags = json.loads(getattr(company, "bot_flags", None) or "{}")
    except Exception:
        flags = {}
    return render(request, "company/bot_settings.html", {
        "active": "bot", "company_name": company.name, "user_name": user.full_name,
        "greeting": company.greeting_message or "", "language": company.bot_language or "both",
        "currency": company.currency,
        "ask_size_help": flags.get("ask_size_help", True),
        "ask_payment_proof": flags.get("ask_payment_proof", True),
        "ask_delivery": flags.get("ask_delivery", True),
        "calc_delivery_fee": flags.get("calc_delivery_fee", False),
        "offer_pidgin": flags.get("offer_pidgin", True),
        "saved": request.query_params.get("saved"),
    })


@app.post("/company/bot-settings")
async def company_bot_save(
    request: Request,
    greeting: Optional[str] = Form(None), language: str = Form("both"), currency: str = Form("NGN"),
    ask_size_help: Optional[str] = Form(None),
    ask_payment_proof: Optional[str] = Form(None),
    ask_delivery: Optional[str] = Form(None),
    calc_delivery_fee: Optional[str] = Form(None),
    offer_pidgin: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    import json
    company = await company_ctx(user, db)
    if greeting is not None:
        company.greeting_message = greeting
    company.bot_language = language
    company.currency = currency
    flags = {
        "ask_size_help": ask_size_help is not None,
        "ask_payment_proof": ask_payment_proof is not None,
        "ask_delivery": ask_delivery is not None,
        "calc_delivery_fee": calc_delivery_fee is not None,
        "offer_pidgin": offer_pidgin is not None,
    }
    company.bot_flags = json.dumps(flags)
    await db.commit()
    return RedirectResponse("/company/bot-settings?saved=1", status_code=303)



@app.post("/company/whatsapp")
async def company_wa_save(
    request: Request,
    phone_number_id: str = Form(...),
    access_token: Optional[str] = Form(None),
    display_number: Optional[str] = Form(None),
    waba_id: Optional[str] = Form(None),
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.models.company import CompanyWhatsAppNumber
    company = await company_ctx(user, db)
    pid = (phone_number_id or "").strip()
    if not pid.isdigit():
        return RedirectResponse("/company/whatsapp?error=phone_id", status_code=303)
    waba = (waba_id or "").strip() or None
    if waba and not waba.isdigit():
        return RedirectResponse("/company/whatsapp?error=waba", status_code=303)
    token = (access_token or "").strip()
    disp = (display_number or "").strip() or None

    existing = (await db.execute(
        select(CompanyWhatsAppNumber).where(CompanyWhatsAppNumber.company_id == company.id)
    )).scalars().first()
    if existing:
        existing.phone_number_id = pid
        existing.waba_id = waba
        existing.display_number = disp
        if token:
            existing.access_token = token
        existing.is_active = True
        if not existing.access_token:
            return RedirectResponse("/company/whatsapp?error=token", status_code=303)
    else:
        if not token:
            return RedirectResponse("/company/whatsapp?error=token", status_code=303)
        db.add(CompanyWhatsAppNumber(
            company_id=company.id,
            phone_number_id=pid,
            waba_id=waba,
            access_token=token,
            display_number=disp,
            is_active=True,
        ))
    await db.commit()
    return RedirectResponse("/company/whatsapp?saved=1", status_code=303)


@app.get("/company/whatsapp", response_class=HTMLResponse)
async def company_wa(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    from app.models.company import CompanyWhatsAppNumber
    company = await company_ctx(user, db)
    wa = (await db.execute(
        select(CompanyWhatsAppNumber).where(CompanyWhatsAppNumber.company_id == company.id)
    )).scalars().first()
    saved = request.query_params.get("saved")
    err = request.query_params.get("error")
    error_msg = None
    if err == "phone_id":
        error_msg = "Phone Number ID must be digits only (from Meta API Setup)."
    elif err == "waba":
        error_msg = "WhatsApp Business Account ID must be digits only (not an email)."
    elif err == "token":
        error_msg = "Access token is required for the first connection."
    return render(request, "company/whatsapp.html", {
        "active": "whatsapp", "company_name": company.name, "user_name": user.full_name,
        "wa": wa, "saved": saved, "error": error_msg,
    })


@app.get("/company/settings", response_class=HTMLResponse)
async def company_settings(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    company = await company_ctx(user, db)
    return render(request, "company/settings.html", {
        "active": "settings", "company_name": company.name, "user_name": user.full_name,
        "user_email": user.email, "success": None, "error": None,
    })


@app.post("/company/settings")
async def company_settings_save(
    request: Request, full_name: str = Form(...), email: str = Form(...),
    current_password: Optional[str] = Form(None), new_password: Optional[str] = Form(None),
    confirm_password: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    user.full_name = full_name.strip()
    user.email = email.strip().lower()
    err = None
    if new_password:
        if new_password != (confirm_password or ""):
            err = "Passwords do not match"
        elif not current_password or not verify_password(current_password, user.hashed_password):
            err = "Current password incorrect"
        else:
            user.hashed_password = hash_password(new_password)
    if not err:
        await db.commit()
    return render(request, "company/settings.html", {
        "active": "settings", "company_name": company.name, "user_name": user.full_name,
        "user_email": user.email,
        "success": None if err else "Profile updated. Platform Admin can see name and email changes.",
        "error": err,
    })


# ---------- WhatsApp webhook stubs ----------
@app.get("/api/webhook/whatsapp")
async def wa_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Verification failed")


@app.post("/api/webhook/whatsapp")
async def wa_incoming(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services.bot_engine import handle_inbound
    body = await request.json()
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id") or ""
                for msg in value.get("messages", []) or []:
                    from_wa = msg.get("from") or ""
                    text = None
                    media_id = None
                    button_id = None
                    list_id = None
                    if msg.get("type") == "text":
                        text = (msg.get("text") or {}).get("body")
                    elif msg.get("type") == "image":
                        media_id = (msg.get("image") or {}).get("id")
                        text = (msg.get("image") or {}).get("caption") or ""
                    elif msg.get("type") == "document":
                        media_id = (msg.get("document") or {}).get("id")
                    elif msg.get("type") == "interactive":
                        inter = msg.get("interactive") or {}
                        if inter.get("type") == "button_reply":
                            button_id = (inter.get("button_reply") or {}).get("id")
                            text = (inter.get("button_reply") or {}).get("title") or ""
                        elif inter.get("type") == "list_reply":
                            list_id = (inter.get("list_reply") or {}).get("id")
                            text = (inter.get("list_reply") or {}).get("title") or ""
                    if phone_number_id and from_wa:
                        await handle_inbound(
                            db,
                            phone_number_id=phone_number_id,
                            from_wa=from_wa,
                            text=text,
                            media_id=media_id,
                            button_id=button_id,
                            list_id=list_id,
                        )
    except Exception as e:
        print("Webhook error:", e)
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok", "app": "Client-RaQ"}
