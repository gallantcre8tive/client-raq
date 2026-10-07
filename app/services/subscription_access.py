"""Central access control: trial, paid, suspended, channel entitlements."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.billing_service import (
    get_company_subscription,
    get_pricing,
    subscription_is_live,
)


async def access_snapshot(db: AsyncSession, company_id: int) -> dict[str, Any]:
    sub = await get_company_subscription(db, company_id)
    pricing = await get_pricing(db)
    live = subscription_is_live(sub)
    now = datetime.now(timezone.utc)

    status = "none"
    hours_left = None
    days_left = None
    if sub:
        status = sub.status or "none"
        if sub.status == "trial" and sub.trial_ends_at:
            end = sub.trial_ends_at if sub.trial_ends_at.tzinfo else sub.trial_ends_at.replace(tzinfo=timezone.utc)
            sec = (end - now).total_seconds()
            hours_left = max(0, sec / 3600.0)
        if sub.status == "active" and sub.subscription_ends_at:
            end = sub.subscription_ends_at if sub.subscription_ends_at.tzinfo else sub.subscription_ends_at.replace(tzinfo=timezone.utc)
            sec = (end - now).total_seconds()
            days_left = max(0, sec / 86400.0)
            if sec <= 0:
                live = False
                status = "expired"

    return {
        "sub": sub,
        "live": live,
        "status": status,
        "hours_left": hours_left,
        "days_left": days_left,
        "channel_whatsapp": bool(sub.channel_whatsapp) if sub else True,
        "channel_telegram": bool(sub.channel_telegram) if sub else False,
        "guide_unlocked": bool(sub.guide_unlocked) if sub else False,
        "trial_message_limit": int(pricing.get("trial_message_limit") or 50),
        "message_count_trial": int(sub.message_count_trial or 0) if sub else 0,
        "pricing": pricing,
    }


async def assert_channel_allowed(db: AsyncSession, company_id: int, channel: str) -> tuple[bool, str]:
    snap = await access_snapshot(db, company_id)
    if not snap["live"]:
        return False, "Your Client-RaQ subscription or trial is not active. Open Billing on clientraq.com to renew."
    if channel == "whatsapp" and not snap["channel_whatsapp"]:
        return False, "WhatsApp is not included in your plan. Upgrade under Billing."
    if channel == "telegram" and not snap["channel_telegram"]:
        return False, "Telegram is not included in your plan. Add Telegram under Billing."
    return True, ""


async def assert_bot_may_reply(db: AsyncSession, company_id: int) -> tuple[bool, str]:
    snap = await access_snapshot(db, company_id)
    if snap["status"] == "suspended":
        return False, "This assistant is suspended."
    if not snap["live"]:
        return False, "This business assistant is paused because the subscription ended. Please ask the business to renew on Client-RaQ, or try again later."
    if snap["status"] == "trial":
        if snap["message_count_trial"] >= snap["trial_message_limit"]:
            return False, "This free trial has reached its message limit. Please subscribe on Client-RaQ to continue."
    return True, ""
