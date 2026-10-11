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
    # Attach cid from token if present (multi-tab isolation)
    try:
        if payload.get("cid") is not None:
            setattr(user, "_token_cid", int(payload["cid"]))
    except Exception:
        pass
    return user


def _cid_hint(request: Request) -> int | None:
    """Preferred company id from query, header, or cookie."""
    for key in ("cid", "company_id", "co"):
        raw = request.query_params.get(key)
        if raw and str(raw).isdigit():
            return int(raw)
    hdr = request.headers.get("x-crq-company") or request.headers.get("x-company-id")
    if hdr and str(hdr).isdigit():
        return int(hdr)
    # soft preference cookie (not auth — only selects which session cookie to read)
    pref = request.cookies.get("crq_active_cid")
    if pref and str(pref).isdigit():
        return int(pref)
    return None


def _company_session_cookies(request: Request) -> list[tuple[int, str]]:
    """All per-company session cookies present in the request."""
    out: list[tuple[int, str]] = []
    for name, val in (request.cookies or {}).items():
        if not val:
            continue
        if name.startswith("crq_cs_") and name[7:].isdigit():
            out.append((int(name[7:]), val))
    return out


async def _resolve_company_token(request: Request) -> str | None:
    """
    Multi-tab safe company auth:
    - Prefer cookie crq_cs_{cid} when cid is known (query/header/pref)
    - If only one crq_cs_* cookie exists, use it
    - Else fall back to legacy crq_company_session / crq_session
    """
    hint = _cid_hint(request)
    if hint is not None:
        tok = request.cookies.get(f"crq_cs_{hint}")
        if tok:
            return tok

    candidates = _company_session_cookies(request)
    if len(candidates) == 1:
        return candidates[0][1]
    if hint is not None:
        for cid, tok in candidates:
            if cid == hint:
                return tok

    # Legacy single cookie (last login wins — used when only one company is active)
    return (
        request.cookies.get(getattr(settings, "SESSION_COOKIE_COMPANY", "crq_company_session"))
        or request.cookies.get(getattr(settings, "SESSION_COOKIE", "crq_session"))
    )


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Generic: try path-appropriate cookie, then legacy crq_session."""
    path = request.url.path or ""
    token = None
    if path.startswith("/platform") or path.startswith("/api/platform"):
        token = request.cookies.get(getattr(settings, "SESSION_COOKIE_PLATFORM", "crq_platform_session"))
    elif path.startswith("/company") or path.startswith("/api/company"):
        token = await _resolve_company_token(request)
    if not token:
        token = request.cookies.get(settings.SESSION_COOKIE)
    user = await _user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_platform(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = (
        request.cookies.get(getattr(settings, "SESSION_COOKIE_PLATFORM", "crq_platform_session"))
        or request.cookies.get(settings.SESSION_COOKIE)
    )
    user = await _user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if _role_str(user.role) != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform admin only")
    return user


async def require_company(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = await _resolve_company_token(request)
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

    # If client asked for a specific company, enforce match (prevents cross-tenant bleed)
    hint = _cid_hint(request)
    if hint is not None and int(user.company_id) != int(hint):
        # Try the cookie for the requested company specifically
        alt = request.cookies.get(f"crq_cs_{hint}")
        alt_user = await _user_from_token(alt, db)
        if alt_user and getattr(alt_user, "company_id", None) and int(alt_user.company_id) == int(hint):
            user = alt_user
        else:
            raise HTTPException(
                status_code=403,
                detail="Session does not match this company. Open this company login in this tab.",
            )

    # Attach business_type for nav labels on every company page
    try:
        from sqlalchemy import text as sa_text
        row = (await db.execute(
            sa_text("SELECT business_type, name FROM companies WHERE id = :id"),
            {"id": int(user.company_id)},
        )).mappings().first()
        if row:
            request.state.company_business_type = (row.get("business_type") or "printing")
            request.state.company_display_name = row.get("name") or ""
            request.state.company_id = int(user.company_id)
        else:
            request.state.company_business_type = "printing"
            request.state.company_display_name = ""
            request.state.company_id = int(user.company_id)
    except Exception as e:
        print("require_company bt", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        request.state.company_business_type = getattr(request.state, "company_business_type", "printing")
        request.state.company_display_name = getattr(request.state, "company_display_name", "")
        request.state.company_id = int(getattr(user, "company_id", 0) or 0)
    return user
