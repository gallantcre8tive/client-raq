"""Subscription expiry reminders + status flip to expired.

Run via /internal/cron/subscription-emails?key=CRON_SECRET
or opportunistically when admin opens the dashboard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("client_raq.sub_emails")

PLAN_LABELS = {
    "monthly": "monthly plan",
    "six_month": "6-month plan",
    "yearly": "yearly plan",
    "trial": "free trial",
}


async def ensure_reminder_columns(db: AsyncSession) -> None:
    for stmt in (
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_3d_sent_at TIMESTAMPTZ",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS expired_email_sent_at TIMESTAMPTZ",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS reminder_1d_sent_at TIMESTAMPTZ",
    ):
        try:
            await db.execute(text(stmt))
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def process_subscription_emails(db: AsyncSession) -> dict[str, Any]:
    """Send 3-day / 1-day warnings and expired notices. Idempotent per subscription."""
    await ensure_reminder_columns(db)
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "remind_3d": 0, "remind_1d": 0, "expired": 0, "errors": 0}

    try:
        rows = (await db.execute(text("""
            SELECT s.id AS sub_id, s.company_id, s.status, s.plan_code,
                   s.trial_ends_at, s.subscription_ends_at,
                   s.reminder_3d_sent_at, s.reminder_1d_sent_at, s.expired_email_sent_at,
                   c.name AS company_name, c.email AS company_email
            FROM subscriptions s
            JOIN companies c ON c.id = s.company_id
            WHERE s.status IN ('trial', 'active', 'expired')
        """))).mappings().all()
    except Exception as e:
        # older schema without reminder columns — try without those selects after ensure
        log.warning("sub_email_query %s", e)
        try:
            await db.rollback()
        except Exception:
            pass
        return {**stats, "error": str(e)}

    from app.services.email_service import (
        send_subscription_expiring_email,
        send_subscription_expired_email,
    )

    for row in rows:
        stats["checked"] += 1
        email = (row.get("company_email") or "").strip()
        if not email or "@" not in email:
            continue
        name = (row.get("company_name") or "there").strip() or "there"
        status = (row.get("status") or "").lower()
        plan_code = (row.get("plan_code") or status or "subscription").lower()

        if status == "trial":
            end = _aware(row.get("trial_ends_at"))
            plan_label = PLAN_LABELS["trial"]
        else:
            end = _aware(row.get("subscription_ends_at"))
            plan_label = PLAN_LABELS.get(plan_code, plan_code.replace("_", " ") or "subscription")

        if not end:
            continue

        days_left = (end - now).total_seconds() / 86400.0
        ends_on = end.strftime("%d %b %Y")

        # Past end → expired email + status
        if days_left <= 0:
            if not row.get("expired_email_sent_at"):
                try:
                    send_subscription_expired_email(
                        email, company_name=name, plan_label=plan_label
                    )
                    await db.execute(text("""
                        UPDATE subscriptions
                        SET status = CASE WHEN status = 'suspended' THEN status ELSE 'expired' END,
                            expired_email_sent_at = :now,
                            updated_at = :now
                        WHERE id = :id
                    """), {"now": now, "id": int(row["sub_id"])})
                    await db.commit()
                    stats["expired"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("expired_email %s %s", row.get("sub_id"), e)
                    try:
                        await db.rollback()
                    except Exception:
                        pass
            elif status in ("trial", "active"):
                try:
                    await db.execute(text("""
                        UPDATE subscriptions SET status = 'expired', updated_at = :now
                        WHERE id = :id AND status IN ('trial', 'active')
                    """), {"now": now, "id": int(row["sub_id"])})
                    await db.commit()
                except Exception:
                    try:
                        await db.rollback()
                    except Exception:
                        pass
            continue

        # 1-day reminder
        if 0 < days_left <= 1.5 and not row.get("reminder_1d_sent_at"):
            try:
                send_subscription_expiring_email(
                    email,
                    company_name=name,
                    days_left=max(1, int(round(days_left))),
                    plan_label=plan_label,
                    ends_on=ends_on,
                )
                await db.execute(text("""
                    UPDATE subscriptions SET reminder_1d_sent_at = :now, updated_at = :now
                    WHERE id = :id
                """), {"now": now, "id": int(row["sub_id"])})
                await db.commit()
                stats["remind_1d"] += 1
            except Exception as e:
                stats["errors"] += 1
                log.warning("remind_1d %s %s", row.get("sub_id"), e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            continue

        # 3-day reminder (between ~2.5 and 3.5 days, or anywhere in 2–4 if 3d not sent)
        if 1.5 < days_left <= 3.5 and not row.get("reminder_3d_sent_at"):
            try:
                d = max(1, int(round(days_left)))
                send_subscription_expiring_email(
                    email,
                    company_name=name,
                    days_left=d,
                    plan_label=plan_label,
                    ends_on=ends_on,
                )
                await db.execute(text("""
                    UPDATE subscriptions SET reminder_3d_sent_at = :now, updated_at = :now
                    WHERE id = :id
                """), {"now": now, "id": int(row["sub_id"])})
                await db.commit()
                stats["remind_3d"] += 1
            except Exception as e:
                stats["errors"] += 1
                log.warning("remind_3d %s %s", row.get("sub_id"), e)
                try:
                    await db.rollback()
                except Exception:
                    pass

    return stats
