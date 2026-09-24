from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User, UserRole
from app.core.security import decode_token
from app.config import get_settings

settings = get_settings()

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid session")
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    return user

async def require_platform(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="Platform admin only")
    return user

async def require_company(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.COMPANY_ADMIN, UserRole.COMPANY_STAFF):
        raise HTTPException(status_code=403, detail="Company staff only")
    if not user.company_id:
        raise HTTPException(status_code=403, detail="No company linked")
    return user
