"""Company bell list/mark/clear — inserts via admin_notify."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admin_notify import ensure_notif_table, notify_company as _admin_notify


async def notify_company(
    db: AsyncSession,
    company_id: int,
    *,
    title: str,
    body: str,
    priority: str = "high",
    kind: str = "general",
    link_path: str = "/company/dashboard",
    conversation_id: Optional[int] = None,
) -> bool:
    nid = await _admin_notify(
        db,
        company_id=int(company_id),
        title=title,
        body=body,
        priority=priority,
        kind=kind,
        link_path=link_path,
        conversation_id=conversation_id,
    )
    return nid is not None


def _row_to_item(n) -> dict:
    nid = int(n["id"])
    title = n.get("title") or "Notification"
    body = n.get("body") or ""
    pri = str(n.get("priority") or "high").lower()
    kind = str(n.get("kind") or "general").lower()
    if kind in ("platform", "platform_message") or "platform" in title.lower() or "message from" in title.lower():
        pri = "high"
        kind = "platform"
    created = n.get("created_at")
    time_s = ""
    if created is not None:
        try:
            time_s = created.strftime("%d %b %Y · %H:%M")
        except Exception:
            time_s = str(created)[:16]
    conv_id = n.get("conversation_id")
    link = (n.get("link_path") or "").strip() if "link_path" in n else ""
    if not link:
        link = f"/company/messages?c={conv_id}" if conv_id else "/company/dashboard"
    return {
        "id": nid,
        "title": title,
        "body": body,
        "priority": pri,
        "kind": kind,
        "conversation_id": conv_id,
        "link": link,
        "created_at": time_s,
        "time": time_s,
        "is_read": bool(n.get("is_read")) if n.get("is_read") is not None else False,
    }


async def list_company_notifications(db: AsyncSession, company_id: int, limit: int = 40) -> list[dict]:
    await ensure_notif_table(db)
    cid = int(company_id)
    out: list[dict] = []
    queries = [
        """SELECT id, title, body, COALESCE(priority, 'high') AS priority,
                  conversation_id, COALESCE(link_path, '') AS link_path,
                  COALESCE(kind, 'general') AS kind, created_at,
                  COALESCE(is_read, false) AS is_read
           FROM admin_notifications
           WHERE company_id = :cid AND COALESCE(is_read, false) = false
           ORDER BY id DESC LIMIT :lim""",
        """SELECT id, title, body, created_at
           FROM admin_notifications
           WHERE company_id = :cid
           ORDER BY id DESC LIMIT :lim""",
    ]
    for sql in queries:
        try:
            rows = (await db.execute(text(sql), {"cid": cid, "lim": int(limit)})).mappings().all()
            for n in rows:
                item = _row_to_item(n)
                # second query has no is_read — only include if not already marked; show all unread path
                if "is_read" in n and n.get("is_read"):
                    continue
                out.append(item)
            if out or sql == queries[0]:
                # if first query succeeded (even empty), use it
                if sql == queries[0]:
                    return out
                break
        except Exception as e:
            print("list_company_notifications try", type(e).__name__, e)
            try:
                await db.rollback()
            except Exception:
                pass
    return out[:limit]


async def mark_notification_read(db: AsyncSession, company_id: int, nid: int) -> None:
    await ensure_notif_table(db)
    try:
        await db.execute(text("""
            UPDATE admin_notifications SET is_read = true
            WHERE id = :nid AND company_id = :cid
        """), {"nid": int(nid), "cid": int(company_id)})
        await db.commit()
    except Exception as e:
        print("mark_read", e)
        try:
            await db.rollback()
        except Exception:
            pass


async def mark_all_read(db: AsyncSession, company_id: int) -> None:
    await ensure_notif_table(db)
    try:
        await db.execute(text("""
            UPDATE admin_notifications SET is_read = true
            WHERE company_id = :cid AND COALESCE(is_read, false) = false
        """), {"cid": int(company_id)})
        await db.commit()
    except Exception as e:
        print("mark_all_read", e)
        try:
            await db.rollback()
        except Exception:
            pass


async def clear_notifications(db: AsyncSession, company_id: int) -> None:
    await ensure_notif_table(db)
    try:
        await db.execute(text("DELETE FROM admin_notifications WHERE company_id = :cid"), {"cid": int(company_id)})
        await db.commit()
    except Exception as e:
        print("clear_notifications", e)
        try:
            await db.rollback()
        except Exception:
            pass
