"""Persistent company admin notifications — raw SQL only (no ORM enum issues)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def ensure_notif_table(db: AsyncSession) -> None:
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
    try:
        await db.execute(text(
            "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS kind VARCHAR(40) DEFAULT 'general'"
        ))
    except Exception:
        pass
    await db.commit()


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
        return None
    try:
        await ensure_notif_table(db)
    except Exception as e:
        print("ensure_notif_table", e)
        try:
            await db.rollback()
        except Exception:
            pass
    try:
        row = (await db.execute(text("""
            INSERT INTO admin_notifications
                (company_id, conversation_id, title, body, priority, is_read, link_path, kind, created_at)
            VALUES
                (:cid, :convid, :title, :body, :pri, false, :link, :kind, NOW())
            RETURNING id
        """), {
            "cid": int(company_id),
            "convid": int(conversation_id) if conversation_id else None,
            "title": (title or "Notification")[:200],
            "body": (body or "")[:4000],
            "pri": (priority or "high")[:20],
            "link": (link_path or "/company/dashboard")[:300],
            "kind": (kind or "general")[:40],
        })).first()
        await db.commit()
        nid = int(row[0]) if row else None
        print("admin_notify_ok", "company_id=", company_id, "id=", nid, "title=", (title or "")[:40], "kind=", kind)
        return nid
    except Exception as e:
        print("admin_notify_FAIL", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        try:
            await db.execute(text("""
                INSERT INTO admin_notifications (company_id, title, body, priority, is_read, created_at)
                VALUES (:cid, :title, :body, 'high', false, NOW())
            """), {"cid": int(company_id), "title": (title or "Notification")[:200], "body": (body or "")[:4000]})
            await db.commit()
            print("admin_notify_minimal_ok", company_id)
        except Exception as e2:
            print("admin_notify_minimal_FAIL", type(e2).__name__, e2)
            try:
                await db.rollback()
            except Exception:
                pass
        return None


async def notify_new_customer(
    db: AsyncSession,
    *,
    company_id: int,
    customer_label: str,
    conversation_id: int | None = None,
) -> None:
    await notify_company(
        db,
        company_id=company_id,
        title="New customer",
        body=f"A new customer started chatting: {customer_label}",
        priority="normal",
        conversation_id=conversation_id,
        link_path=f"/company/messages?c={conversation_id}" if conversation_id else "/company/messages",
        kind="new_customer",
    )


async def notify_new_order(
    db: AsyncSession,
    *,
    company_id: int,
    order_id: int | None,
    service_name: str,
    total: float | None,
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
) -> None:
    amt = f"{currency} {amount:,.0f}" if amount is not None else "see screenshot"
    body = f"Payment screenshot received ({amt})"
    if customer_label:
        body += f" from {customer_label}"
    body += ". Open to confirm or reject."
    link = attachment_path or (f"/company/orders/{order_id}" if order_id else "/company/orders")
    await notify_company(
        db,
        company_id=company_id,
        title="Payment to confirm",
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
) -> None:
    await notify_company(
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
