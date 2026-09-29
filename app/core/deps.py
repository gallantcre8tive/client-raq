from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User, UserRole
from app.core.security import decode_token
from app.config import get_settings

settings = get_settings()


def _role_str(role) -> str:
    if role is None:
        return ""
    if isinstance(role, UserRole):
        return role.value
    if hasattr(role, "value"):
        return str(role.value).lower().strip()
    s = str(role).strip()
    aliases = {
        "PLATFORM_ADMIN": "platform_admin",
        "COMPANY_ADMIN": "company_admin",
        "COMPANY_STAFF": "company_staff",
        "platform_admin": "platform_admin",
        "company_admin": "company_admin",
        "company_staff": "company_staff",
    }
    if s in aliases:
        return aliases[s]
    s2 = s.lower().replace(" ", "_")
    if "." in s2:
        s2 = s2.split(".")[-1]
    return aliases.get(s2, s2)


async def _user_from_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        uid = int(payload["sub"])
    except (TypeError, ValueError):
        return None
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    return user


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Generic: try path-appropriate cookie, then legacy crq_session."""
    path = request.url.path or ""
    token = None
    if path.startswith("/platform"):
        token = request.cookies.get(settings.SESSION_COOKIE_PLATFORM)
    elif path.startswith("/company"):
        token = request.cookies.get(settings.SESSION_COOKIE_COMPANY)
    if not token:
        token = request.cookies.get(settings.SESSION_COOKIE)
    user = await _user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_platform(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = (
        request.cookies.get(settings.SESSION_COOKIE_PLATFORM)
        or request.cookies.get(settings.SESSION_COOKIE)
    )
    user = await _user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if _role_str(user.role) != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    return user


async def require_company(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = (
        request.cookies.get(settings.SESSION_COOKIE_COMPANY)
        or request.cookies.get(settings.SESSION_COOKIE)
    )
    user = await _user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    role = _role_str(user.role)
    if role not in ("company_admin", "company_staff"):
        raise HTTPException(
            status_code=403,
            detail="Company staff only. Log in at /company/login with a company account.",
        )
    if not getattr(user, "company_id", None):
        raise HTTPException(status_code=403, detail="No company linked to this account")
    return user
