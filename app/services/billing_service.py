"""Trial, subscription activation, access checks, pricing helpers."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlatformPricing, Subscription, Payment, RegistrationToken


DEFAULT_PRICES = {
    "monthly_ngn": 18000.0,
    "six_month_ngn": 102000.0,
    "yearly_ngn": 198000.0,
    "channel_addon_monthly_ngn": 18000.0,
    "guide_fee_ngn": 5000.0,
    "trial_hours": 48,
    "trial_enabled": True,
    "trial_message_limit": 50,
    "six_month_includes_both_channels": True,
    "yearly_includes_both_channels": True,
}


async def ensure_billing_tables(db: AsyncSession) -> None:
    stmts = [
        """CREATE TABLE IF NOT EXISTS platform_pricing (
            id SERIAL PRIMARY KEY,
            monthly_ngn DOUBLE PRECISION DEFAULT 18000,
            six_month_ngn DOUBLE PRECISION DEFAULT 102000,
            yearly_ngn DOUBLE PRECISION DEFAULT 198000,
            channel_addon_monthly_ngn DOUBLE PRECISION DEFAULT 18000,
            guide_fee_ngn DOUBLE PRECISION DEFAULT 5000,
            trial_hours INTEGER DEFAULT 48,
            trial_enabled BOOLEAN DEFAULT true,
            trial_message_limit INTEGER DEFAULT 50,
            six_month_includes_both_channels BOOLEAN DEFAULT true,
            yearly_includes_both_channels BOOLEAN DEFAULT true,
            support_whatsapp VARCHAR(40),
            support_email VARCHAR(255),
            social_instagram VARCHAR(200),
            social_x VARCHAR(200),
            social_facebook VARCHAR(200),
            social_tiktok VARCHAR(200),
            social_linkedin VARCHAR(200),
            social_youtube VARCHAR(200),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            status VARCHAR(30) DEFAULT 'trial',
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            trial_ends_at TIMESTAMPTZ,
            subscription_ends_at TIMESTAMPTZ,
            message_count_trial INTEGER DEFAULT 0,
            guide_unlocked BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS platform_payments (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            email VARCHAR(255),
            reference VARCHAR(100) UNIQUE,
            amount_kobo INTEGER DEFAULT 0,
            currency VARCHAR(10) DEFAULT 'NGN',
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            include_guide BOOLEAN DEFAULT false,
            status VARCHAR(30) DEFAULT 'pending',
            paystack_raw TEXT,
            registration_token VARCHAR(64),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            paid_at TIMESTAMPTZ
        )""",
        """CREATE TABLE IF NOT EXISTS registration_tokens (
            id SERIAL PRIMARY KEY,
            token VARCHAR(64) UNIQUE,
            email VARCHAR(255),
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            include_guide BOOLEAN DEFAULT false,
            payment_reference VARCHAR(100),
            amount_kobo INTEGER DEFAULT 0,
            used BOOLEAN DEFAULT false,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS business_type VARCHAR(40) DEFAULT 'printing'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS website_url VARCHAR(300)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS telegram_bot_token VARCHAR(200)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN DEFAULT false",
    ]
    for sql in stmts:
        try:
            await db.execute(text(sql))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass


async def get_pricing(db: AsyncSession) -> dict[str, Any]:
    await ensure_billing_tables(db)
    row = (await db.execute(select(PlatformPricing).where(PlatformPricing.id == 1))).scalars().first()
    if not row:
        row = PlatformPricing(id=1, **{k: v for k, v in DEFAULT_PRICES.items() if k in (
            "monthly_ngn", "six_month_ngn", "yearly_ngn", "channel_addon_monthly_ngn",
            "guide_fee_ngn", "trial_hours", "trial_enabled", "trial_message_limit",
            "six_month_includes_both_channels", "yearly_includes_both_channels",
        )})
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
            return dict(DEFAULT_PRICES)
    return {
        "monthly_ngn": float(row.monthly_ngn or 18000),
        "six_month_ngn": float(row.six_month_ngn or 102000),
        "yearly_ngn": float(row.yearly_ngn or 198000),
        "channel_addon_monthly_ngn": float(row.channel_addon_monthly_ngn or 18000),
        "guide_fee_ngn": float(row.guide_fee_ngn or 5000),
        "trial_hours": int(row.trial_hours or 48),
        "trial_enabled": bool(row.trial_enabled if row.trial_enabled is not None else True),
        "trial_message_limit": int(row.trial_message_limit or 50),
        "six_month_includes_both_channels": bool(row.six_month_includes_both_channels),
        "yearly_includes_both_channels": bool(row.yearly_includes_both_channels),
        "support_whatsapp": row.support_whatsapp or "",
        "support_email": row.support_email or "",
        "social_instagram": getattr(row, "social_instagram", None) or "https://instagram.com/client_raq",
        "social_x": getattr(row, "social_x", None) or "https://x.com/client_raq",
        "social_facebook": getattr(row, "social_facebook", None) or "https://facebook.com/client_raq",
        "social_tiktok": getattr(row, "social_tiktok", None) or "https://tiktok.com/@client_raq",
        "social_linkedin": getattr(row, "social_linkedin", None) or "https://linkedin.com/company/client_raq",
        "social_youtube": getattr(row, "social_youtube", None) or "",
        "social_whatsapp": getattr(row, "social_whatsapp", None) or "https://wa.me/2349169158961",
        "social_telegram": getattr(row, "social_telegram", None) or "https://t.me/client_raq",
    }


def plan_duration_days(plan_code: str) -> int:
    return {"monthly": 30, "six_month": 180, "yearly": 365}.get(plan_code, 30)


def compute_checkout_amount_kobo(
    pricing: dict[str, Any],
    plan_code: str,
    *,
    want_whatsapp: bool,
    want_telegram: bool,
    include_guide: bool,
) -> tuple[int, dict[str, Any]]:
    """Returns (amount_kobo, breakdown)."""
    base = {
        "monthly": pricing["monthly_ngn"],
        "six_month": pricing["six_month_ngn"],
        "yearly": pricing["yearly_ngn"],
    }.get(plan_code, pricing["monthly_ngn"])

    channels_included = 1
    addon = 0.0
    both_free = (
        (plan_code == "six_month" and pricing.get("six_month_includes_both_channels"))
        or (plan_code == "yearly" and pricing.get("yearly_includes_both_channels"))
    )
    if want_whatsapp and want_telegram:
        if both_free or plan_code in ("six_month", "yearly"):
            channels_included = 2
        else:
            # monthly: second channel is add-on
            addon = float(pricing.get("channel_addon_monthly_ngn") or 0)
            channels_included = 2
    elif want_whatsapp or want_telegram:
        channels_included = 1
    else:
        want_whatsapp = True
        channels_included = 1

    guide = float(pricing.get("guide_fee_ngn") or 0) if include_guide else 0.0
    total_ngn = float(base) + addon + guide
    breakdown = {
        "plan_ngn": float(base),
        "channel_addon_ngn": addon,
        "guide_ngn": guide,
        "total_ngn": total_ngn,
        "channels": channels_included,
        "plan_code": plan_code,
        "channel_whatsapp": bool(want_whatsapp),
        "channel_telegram": bool(want_telegram),
        "include_guide": bool(include_guide),
    }
    return int(round(total_ngn * 100)), breakdown


async def get_company_subscription(db: AsyncSession, company_id: int) -> Subscription | None:
    return (await db.execute(
        select(Subscription).where(Subscription.company_id == company_id).order_by(Subscription.id.desc())
    )).scalars().first()


def subscription_is_live(sub: Subscription | None) -> bool:
    if not sub:
        return False
    if sub.status == "suspended":
        return False
    now = datetime.now(timezone.utc)
    if sub.status == "trial" and sub.trial_ends_at:
        end = sub.trial_ends_at if sub.trial_ends_at.tzinfo else sub.trial_ends_at.replace(tzinfo=timezone.utc)
        return now < end
    if sub.status == "active" and sub.subscription_ends_at:
        end = sub.subscription_ends_at if sub.subscription_ends_at.tzinfo else sub.subscription_ends_at.replace(tzinfo=timezone.utc)
        return now < end
    return False


async def create_trial_subscription(db: AsyncSession, company_id: int, pricing: dict[str, Any]) -> Subscription:
    hours = int(pricing.get("trial_hours") or 48)
    now = datetime.now(timezone.utc)
    sub = Subscription(
        company_id=company_id,
        status="trial",
        plan_code=None,
        channel_whatsapp=True,
        channel_telegram=False,
        trial_ends_at=now + timedelta(hours=hours),
        subscription_ends_at=None,
        guide_unlocked=True,  # short trial guide available
        message_count_trial=0,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def activate_paid_subscription(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    channel_whatsapp: bool,
    channel_telegram: bool,
    include_guide: bool,
) -> Subscription:
    now = datetime.now(timezone.utc)
    days = plan_duration_days(plan_code)
    sub = await get_company_subscription(db, company_id)
    # Stack time: if still active, extend from current end; else from now
    base = now
    if sub and sub.subscription_ends_at and sub.status == "active":
        end = sub.subscription_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end > now:
            base = end
    ends = base + timedelta(days=days)
    if sub:
        sub.status = "active"
        sub.plan_code = plan_code
        sub.channel_whatsapp = channel_whatsapp
        sub.channel_telegram = channel_telegram
        sub.subscription_ends_at = ends
        if include_guide or plan_code in ("six_month", "yearly"):
            sub.guide_unlocked = True
        elif include_guide:
            sub.guide_unlocked = True
        sub.updated_at = now
    else:
        sub = Subscription(
            company_id=company_id,
            status="active",
            plan_code=plan_code,
            channel_whatsapp=channel_whatsapp,
            channel_telegram=channel_telegram,
            trial_ends_at=None,
            subscription_ends_at=ends,
            guide_unlocked=True,
        )
        db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_payment_reference() -> str:
    return "CRQ-" + secrets.token_hex(12).upper()
