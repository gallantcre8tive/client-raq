"""Persistent company admin notifications — raw SQL, multi-fallback inserts."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def ensure_notif_table(db: AsyncSession) -> None:
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_notifications (
                id SERIAL PRIMARY KEY,
                company_id INTEGER,
                conversation_id INTEGER,
                title VARCHAR(200),
                body TEXT,
                priority VARCHAR(20) DEFAULT 'high',
                is_read BOOLEAN DEFAULT false,
                link_path VARCHAR(300),
                kind VARCHAR(40) DEFAULT 'general',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        for col_sql in (
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS priority VARCHAR(20) DEFAULT 'high'",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT false",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS body TEXT",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS conversation_id INTEGER",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS company_id INTEGER",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS link_path VARCHAR(300)",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS kind VARCHAR(40) DEFAULT 'general'",
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        ):
            try:
                await db.execute(text(col_sql))
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass
        await db.commit()
    except Exception as e:
        print("ensure_notif_table", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass


async def notify_company(
    db: AsyncSession,
    *,
    company_id: int,
    title: str,
    body: str,
    priority: str = "high",
    conversation_id: int | None = None,
    link_path: str | None = None,
    kind: str = "general",
) -> int | None:
    if not company_id:
        print("admin_notify skip: no company_id")
        return None
    await ensure_notif_table(db)
    cid = int(company_id)
    title_s = (title or "Notification")[:200]
    body_s = (body or "")[:4000]
    pri = (priority or "high")[:20]
    kind_s = (kind or "general")[:40]
    link = (link_path or "/company/dashboard")[:300]
    convid = int(conversation_id) if conversation_id else None

    attempts = [
        (
            """INSERT INTO admin_notifications
                (company_id, conversation_id, title, body, priority, is_read, link_path, kind, created_at)
             VALUES (:cid, :convid, :title, :body, :pri, false, :link, :kind, NOW())
             RETURNING id""",
            {"cid": cid, "convid": convid, "title": title_s, "body": body_s, "pri": pri, "link": link, "kind": kind_s},
        ),
        (
            """INSERT INTO admin_notifications
                (company_id, title, body, priority, is_read, kind, created_at)
             VALUES (:cid, :title, :body, :pri, false, :kind, NOW())
             RETURNING id""",
            {"cid": cid, "title": title_s, "body": body_s, "pri": pri, "kind": kind_s},
        ),
        (
            """INSERT INTO admin_notifications
                (company_id, title, body, is_read, created_at)
             VALUES (:cid, :title, :body, false, NOW())
             RETURNING id""",
            {"cid": cid, "title": title_s, "body": body_s},
        ),
        (
            """INSERT INTO admin_notifications (company_id, title, body)
             VALUES (:cid, :title, :body)
             RETURNING id""",
            {"cid": cid, "title": title_s, "body": body_s},
        ),
    ]
    for sql, params in attempts:
        try:
            row = (await db.execute(text(sql), params)).first()
            await db.commit()
            nid = int(row[0]) if row else None
            print("admin_notify_ok company_id=", cid, "id=", nid, "title=", title_s[:40], "kind=", kind_s)
            return nid
        except Exception as e:
            print("admin_notify_try", type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
    print("admin_notify_FAIL all attempts company_id=", cid)
    return None


async def notify_new_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int | None = None,
    service_name: str = "",
    total: float | None = None,
    currency: str = "NGN",
    customer_label: str = "",
    conversation_id: int | None = None,
) -> None:
    total_s = f"{currency} {total:,.0f}" if total is not None else "amount pending"
    await notify_company(
        db,
        company_id=company_id,
        title="New order",
        body=f"Order #{order_id or '—'} · {service_name or 'Service'} · {total_s}"
             + (f" · {customer_label}" if customer_label else ""),
        priority="high",
        conversation_id=conversation_id,
        link_path=f"/company/orders/{order_id}" if order_id else "/company/orders",
        kind="new_order",
    )


async def notify_payment_proof(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int | None = None,
    amount: float | None = None,
    currency: str = "NGN",
    customer_label: str = "",
    conversation_id: int | None = None,
    attachment_path: str | None = None,
    ai_summary: str | None = None,
) -> None:
    amt = f"{currency} {amount:,.0f}" if amount is not None else "see screenshot"
    body = f"Payment screenshot received ({amt})"
    if customer_label:
        body += f" from {customer_label}"
    body += ". Admin must verify — AI analysis is not proof of receipt."
    if ai_summary:
        body += "\n" + str(ai_summary)[:1200]
    link = attachment_path or (f"/company/orders/{order_id}" if order_id else "/company/orders")
    await notify_company(
        db,
        company_id=company_id,
        title="Payment proof — verify",
        body=body,
        priority="urgent",
        conversation_id=conversation_id,
        link_path=link,
        kind="payment_proof",
    )


async def notify_platform_message(
    db: AsyncSession,
    *,
    company_id: int,
    body: str,
    sent_by: str = "Platform",
) -> int | None:
    return await notify_company(
        db,
        company_id=company_id,
        title=f"Message from {sent_by}",
        body=(body or "")[:4000],
        priority="high",
        link_path="/company/dashboard",
        kind="platform_message",
    )


async def notify_human_needed(
    db: AsyncSession,
    *,
    company_id: int,
    reason: str,
    conversation_id: int | None = None,
    customer_label: str = "",
) -> None:
    await notify_company(
        db,
        company_id=company_id,
        title="Customer needs you",
        body=(reason or "Customer requested a human") + (f" · {customer_label}" if customer_label else ""),
        priority="urgent",
        conversation_id=conversation_id,
        link_path=f"/company/messages?c={conversation_id}" if conversation_id else "/company/messages",
        kind="needs_human",
    )
