"""Simple audit log for platform and company admin actions."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_audit_table(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            actor_user_id INTEGER,
            actor_email VARCHAR(255),
            company_id INTEGER,
            action VARCHAR(80),
            detail TEXT,
            ip VARCHAR(60),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    await db.commit()


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    detail: str = "",
    actor_user_id: int | None = None,
    actor_email: str | None = None,
    company_id: int | None = None,
    ip: str | None = None,
) -> None:
    try:
        await ensure_audit_table(db)
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
    try:
        await db.execute(text("""
            INSERT INTO audit_logs (actor_user_id, actor_email, company_id, action, detail, ip, created_at)
            VALUES (:uid, :email, :cid, :action, :detail, :ip, NOW())
        """), {
            "uid": actor_user_id,
            "email": (actor_email or "")[:255],
            "cid": company_id,
            "action": (action or "")[:80],
            "detail": (detail or "")[:4000],
            "ip": (ip or "")[:60],
        })
        await db.commit()
    except Exception as e:
        print("audit_write_fail", e)
        try:
            await db.rollback()
        except Exception:
            pass
