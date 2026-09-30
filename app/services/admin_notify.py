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
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
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
                (company_id, conversation_id, title, body, priority, is_read, link_path, created_at)
            VALUES
                (:cid, :convid, :title, :body, :pri, false, :link, NOW())
            RETURNING id
        """), {
            "cid": int(company_id),
            "convid": int(conversation_id) if conversation_id else None,
            "title": (title or "Notification")[:200],
            "body": (body or "")[:4000],
            "pri": (priority or "high")[:20],
            "link": (link_path or "/company/dashboard")[:300],
        })).first()
        await db.commit()
        nid = int(row[0]) if row else None
        print("admin_notify_ok", "company_id=", company_id, "id=", nid, "title=", (title or "")[:40])
        return nid
    except Exception as e:
        print("admin_notify_FAIL", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # last-chance minimal insert
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
