from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User, UserRole
from app.core.security import decode_token
from app.config import get_settings

settings = get_settings()


def _role_str(role) -> str:
    """Normalize role from enum / DB string so checks never false-fail."""
    if role is None:
        return ""
    if isinstance(role, UserRole):
        return role.value  # company_admin
    if hasattr(role, "value"):
        return str(role.value).lower().strip()
    s = str(role).lower().strip()
    # handle "UserRole.COMPANY_ADMIN" or "COMPANY_ADMIN"
    if "." in s:
        s = s.split(".")[-1]
    s = s.replace(" ", "_")
    mapping = {
        "company_admin": "company_admin",
        "companyadmin": "company_admin",
        "admin": "company_admin",
        "company_staff": "company_staff",
        "companystaff": "company_staff",
        "staff": "company_staff",
        "platform_admin": "platform_admin",
        "platformadmin": "platform_admin",
    }
    return mapping.get(s, s)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        # Browser pages: send to login instead of raw JSON when possible
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid session")
    try:
        uid = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid session")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    return user


async def require_platform(user: User = Depends(get_current_user)) -> User:
    if _role_str(user.role) != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    return user


async def require_company(user: User = Depends(get_current_user)) -> User:
    """Allow company_admin and company_staff. Role compare is string-safe."""
    role = _role_str(user.role)
    if role not in ("company_admin", "company_staff"):
        # Common case: still logged in as platform admin in same browser
        if role == "platform_admin":
            raise HTTPException(
                status_code=403,
                detail="You are logged in as Platform Admin. Open /company/login to use a company account (or use a private window).",
            )
        raise HTTPException(status_code=403, detail="Company staff only")
    if not getattr(user, "company_id", None):
        raise HTTPException(status_code=403, detail="No company linked to this account")
    return user
