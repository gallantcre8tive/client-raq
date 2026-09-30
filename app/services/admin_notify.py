"""Create company admin notifications (persistent)."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


async def notify_company(
    db: AsyncSession,
    *,
    company_id: int,
    title: str,
    body: str,
    priority: str = "high",
    conversation_id: int | None = None,
    link_path: str | None = None,
) -> None:
    if not company_id:
        return
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
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await db.execute(text("""
            INSERT INTO admin_notifications
                (company_id, conversation_id, title, body, priority, is_read, link_path, created_at)
            VALUES
                (:cid, :convid, :title, :body, :pri, false, :link, NOW())
        """), {
            "cid": int(company_id),
            "convid": conversation_id,
            "title": (title or "Notification")[:200],
            "body": (body or "")[:4000],
            "pri": (priority or "high")[:20],
            "link": (link_path or "/company/dashboard")[:300],
        })
        await db.commit()
    except Exception as e:
        print("admin_notify", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
