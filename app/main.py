"""Client-RaQ full-stack: frontend UI + multi-tenant backend."""
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from typing import Optional
from datetime import datetime, timedelta, timezone
from fastapi import BackgroundTasks, FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from app.config import get_settings
from app.database import get_db, init_db
from app.models.user import User, UserRole
from app.models.company import Company, CompanyStatus, PaymentDetail, CompanyWhatsAppNumber
from app.models.catalog import Service, ServiceVariant
from app.models.conversation import AdminNotification, Broadcast, Customer, Conversation, Message, Order
from app.models.conversation import Order, Conversation, OrderStatus
from app.core.security import hash_password, verify_password, create_access_token
from app.core.deps import get_current_user, require_platform, require_company

settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title="Client-RaQ")

@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """Surface errors in logs; avoid silent opaque failures on company pages."""
    import traceback
    tb = traceback.format_exc()
    print("UNHANDLED", request.url.path, type(exc).__name__, exc)
    print(tb)
    # For company dashboard always return a usable page instead of blank 500
    if request.url.path.startswith("/company/dashboard"):
        html = """<!DOCTYPE html><html><head><title>Dashboard</title></head><body style="font-family:system-ui;padding:2rem">
        <h1>Dashboard temporarily unavailable</h1>
        <p>Please refresh in a moment. If this continues, contact platform support.</p>
        <p><a href="/company/dashboard">Retry</a> · <a href="/company/login">Login</a></p>
        </body></html>"""
        return HTMLResponse(html, status_code=200)
    return HTMLResponse(
        f"<h1>Server error</h1><pre style='white-space:pre-wrap;font-size:12px'>{type(exc).__name__}: {exc}</pre>",
        status_code=500,
    )

app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
media_path = BASE.parent / "media"
media_path.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)
    except TypeError:
        # Older Starlette/Jinja2 API
        return templates.TemplateResponse(name, ctx, status_code=status_code)


def set_session(response: RedirectResponse, user_id: int) -> RedirectResponse:
    token = create_access_token({"sub": str(user_id)})
    response.set_cookie(
        settings.SESSION_COOKIE, token,
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7,
    )
    return response


def clear_session(response: RedirectResponse) -> RedirectResponse:
    response.delete_cookie(settings.SESSION_COOKIE)
    return response


@app.on_event("startup")
async def startup():
    try:
        await init_db()
    except Exception as e:
        print(f"DB init warning: {e}")


# ---------- Public ----------


async def revenue_stats(db: AsyncSession, company_id: int | None = None) -> dict:
    zero = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    try:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = day_start.replace(day=1)

        async def sum_since(since):
            try:
                from sqlalchemy import text as sa_text
                # Prefer ORM total; fall back if column missing on older DBs
                try:
                    q = select(func.coalesce(func.sum(Order.total), 0)).where(Order.created_at >= since)
                    if company_id is not None:
                        q = q.where(Order.company_id == company_id)
                    q = q.where(Order.status.in_([
                        OrderStatus.PAYMENT_CONFIRMED,
                        OrderStatus.IN_PRODUCTION,
                        OrderStatus.READY,
                        OrderStatus.COMPLETED,
                    ]))
                    return float((await db.execute(q)).scalar() or 0)
                except Exception as inner:
                    print("revenue orm sum:", inner)
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    return 0.0
            except Exception as e:
                print("revenue sum_since:", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
                return 0.0

        return {
            "daily": await sum_since(day_start),
            "weekly": await sum_since(week_start),
            "monthly": await sum_since(month_start),
        }
    except Exception as e:
        print("revenue_stats:", e)
        try:
            await db.rollback()
        except Exception:
            pass
        return zero


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return render(request, "public/landing.html")


# ---------- Platform auth ----------
@app.get("/platform/login", response_class=HTMLResponse)
async def platform_login_page(request: Request):
    return render(request, "auth/platform_login.html", {"error": None})


@app.post("/platform/login")
async def platform_login(
    request: Request, email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(User).where(User.email == email.strip().lower()))
    user = r.scalar_one_or_none()
    if not user or user.role != UserRole.PLATFORM_ADMIN or not verify_password(password, user.hashed_password):
        return render(request, "auth/platform_login.html", {"error": "Invalid email or password"}, 400)
    resp = RedirectResponse("/platform/dashboard", status_code=303)
    return set_session(resp, user.id)


@app.get("/platform/logout")
async def platform_logout():
    return clear_session(RedirectResponse("/platform/login", status_code=303))


# ---------- Platform pages ----------
@app.get("/platform/dashboard", response_class=HTMLResponse)
async def platform_dashboard(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies, company_rows = [], []
    cc = oc = vc = 0
    rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    try:
        companies = (await db.execute(select(Company).order_by(Company.created_at.desc()).limit(20))).scalars().all()
        cc = (await db.execute(select(func.count()).select_from(Company))).scalar() or 0
    except Exception as e:
        print("dash companies:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        oc = (await db.execute(select(func.count()).select_from(Order))).scalar() or 0
    except Exception as e:
        print("dash orders:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        vc = (await db.execute(select(func.count()).select_from(Conversation))).scalar() or 0
    except Exception as e:
        print("dash conv:", e)
        try: await db.rollback()
        except Exception: pass
    try:
        rev = await revenue_stats(db)
    except Exception as e:
        print("dash rev:", e)
        try: await db.rollback()
        except Exception: pass
        rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    for c in companies:
        st = c.status if isinstance(c.status, str) else getattr(c.status, "value", str(c.status))
        company_rows.append({
            "id": c.id, "name": c.name, "slug": c.slug,
            "country": c.country or "—", "currency": c.currency or "NGN",
            "status": st,
            "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "—",
        })
    return render(request, "platform/dashboard.html", {
        "active": "dashboard",
        "user_name": user.full_name or "Platform Admin",
        "companies_count": cc, "orders_count": oc, "conv_count": vc,
        "revenue": f'{rev["monthly"]:,.0f}',
        "revenue_daily": rev["daily"], "revenue_weekly": rev["weekly"], "revenue_monthly": rev["monthly"],
        "companies": company_rows,
    })


@app.get("/platform/companies", response_class=HTMLResponse)
async def platform_companies(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies = (await db.execute(select(Company).order_by(Company.created_at.desc()))).scalars().all()
    rows = [
        {"id": c.id, "name": c.name, "slug": c.slug, "country": c.country, "currency": c.currency,
         "status": (c.status if isinstance(c.status, str) else getattr(c.status, "value", str(c.status))), "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "—"}
        for c in companies
    ]
    return render(request, "platform/companies.html", {
        "active": "companies", "user_name": user.full_name, "companies": rows,
    })


@app.get("/platform/companies/new", response_class=HTMLResponse)
async def add_company_page(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/provision.html", {
        "active": "add", "user_name": user.full_name, "error": None,
    })


@app.post("/platform/companies/new")
async def add_company_submit(
    request: Request,
    name: str = Form(...), slug: str = Form(...), country: str = Form("Nigeria"),
    currency: str = Form("NGN"), phone: Optional[str] = Form(None),
    admin_full_name: str = Form(...), admin_email: str = Form(...), admin_password: str = Form(...),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    slug_c = slug.strip().lower().replace(" ", "-")
    admin_email_c = admin_email.strip().lower()
    name_c = name.strip()
    phone_c = (phone or "").strip() or None
    greet = f"Welcome to {name_c}! How can we help with your printing order today?"
    try:
        if (await db.execute(select(Company).where(Company.slug == slug_c))).scalar_one_or_none():
            return render(request, "platform/provision.html", {
                "active": "add", "user_name": user.full_name or "Admin",
                "error": "Slug already exists. Choose another.",
            }, 400)
        if (await db.execute(select(User).where(User.email == admin_email_c))).scalar_one_or_none():
            return render(request, "platform/provision.html", {
                "active": "add", "user_name": user.full_name or "Admin",
                "error": "That admin email is already registered.",
            }, 400)
        row = (await db.execute(sa_text("""
            INSERT INTO companies (name, slug, country, currency, phone, greeting_message, bot_language, status)
            VALUES (:name, :slug, :country, :currency, :phone, :greet, 'both', 'active')
            RETURNING id
        """), {
            "name": name_c, "slug": slug_c,
            "country": country or "Nigeria", "currency": currency or "NGN",
            "phone": phone_c, "greet": greet,
        })).first()
        company_id = int(row[0])
        db.add(User(
            email=admin_email_c,
            hashed_password=hash_password(admin_password),
            full_name=admin_full_name.strip(),
            role=UserRole.COMPANY_ADMIN,
            company_id=company_id,
            is_active=True,
        ))
        await db.commit()
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        print("add_company ERROR:", repr(e))
        import traceback
        traceback.print_exc()
        return render(request, "platform/provision.html", {
            "active": "add", "user_name": getattr(user, "full_name", None) or "Admin",
            "error": f"Could not create company: {e}",
        }, 400)
    base = str(request.base_url).rstrip("/")
    return render(request, "platform/company_created.html", {
        "active": "companies", "user_name": getattr(user, "full_name", None) or "Admin",
        "company_name": name_c, "company_slug": slug_c,
        "login_url": f"{base}/company/login",
        "admin_email": admin_email_c, "admin_password": admin_password,
    })


@app.post("/platform/companies/{company_id}/suspend")
async def suspend_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        c.status = "suspended"
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.post("/platform/companies/{company_id}/activate")
async def activate_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        c.status = "active"
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.post("/platform/companies/{company_id}/delete")
async def delete_company(company_id: int, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db)):
    c = await db.get(Company, company_id)
    if c:
        await db.delete(c)
        await db.commit()
    return RedirectResponse("/platform/companies", status_code=303)


@app.get("/platform/companies/{company_id}/admins", response_class=HTMLResponse)
async def company_admins_page(
    company_id: int, request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(404)
    admins = (await db.execute(select(User).where(User.company_id == company_id))).scalars().all()
    return render(request, "admin_company_admins.html", {
        "active": "companies", "user_name": user.full_name,
        "company": company, "admins": admins, "error": None, "created": None,
    })


@app.get("/platform/messages", response_class=HTMLResponse)
async def platform_messages(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    companies = (await db.execute(select(Company).order_by(Company.name))).scalars().all()
    notes = [{"company": c.name, "message": c.platform_note} for c in companies if c.platform_note]
    return render(request, "platform/messages.html", {
        "active": "messages", "user_name": user.full_name,
        "companies": companies, "notes": notes,
    })


@app.post("/platform/messages")
async def platform_messages_send(
    company_id: str = Form(...), message: str = Form(...),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    """Send professional note to one company or all. Appears on dashboard + notification bell."""
    text = (message or "").strip()
    if not text:
        return RedirectResponse("/platform/messages", status_code=303)
    targets = []
    if str(company_id) == "all":
        targets = list((await db.execute(select(Company))).scalars().all())
    else:
        try:
            cid = int(company_id)
        except ValueError:
            return RedirectResponse("/platform/messages", status_code=303)
        c = await db.get(Company, cid)
        if c:
            targets = [c]
    for c in targets:
        c.platform_note = text
        db.add(AdminNotification(
            company_id=c.id,
            conversation_id=None,
            title="Message from Client-RaQ Platform",
            body=text[:2000],
            priority="high",
        ))
    await db.commit()
    return RedirectResponse("/platform/messages?sent=1", status_code=303)


@app.get("/platform/infrastructure", response_class=HTMLResponse)
async def platform_infra(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/infrastructure.html", {"active": "infra", "user_name": user.full_name})


@app.get("/platform/settings", response_class=HTMLResponse)
async def platform_settings(request: Request, user: User = Depends(require_platform)):
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "success": None, "support_whatsapp": settings.SUPPORT_WHATSAPP,
    })


@app.post("/platform/settings")
async def platform_settings_save(
    request: Request, full_name: str = Form(...), email: str = Form(...),
    current_password: Optional[str] = Form(None), new_password: Optional[str] = Form(None),
    confirm_password: Optional[str] = Form(None),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    user.full_name = full_name.strip()
    user.email = email.strip().lower()
    if new_password:
        if new_password != (confirm_password or ""):
            return render(request, "platform/settings.html", {
                "active": "settings", "user_name": user.full_name, "user_email": user.email,
                "success": None, "support_whatsapp": settings.SUPPORT_WHATSAPP,
            })
        if current_password and verify_password(current_password, user.hashed_password):
            user.hashed_password = hash_password(new_password)
    await db.commit()
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "success": "Profile updated.", "support_whatsapp": settings.SUPPORT_WHATSAPP,
    })


@app.post("/platform/settings/secrets")
async def platform_secrets_save(
    request: Request,
    support_whatsapp: Optional[str] = Form(None),
    database_url: Optional[str] = Form(None),
    secret_key: Optional[str] = Form(None),
    wa_verify_token: Optional[str] = Form(None),
    llm_api_key: Optional[str] = Form(None),
    user: User = Depends(require_platform),
):
    # Frontend phase stores note; production should write encrypted secrets store / .env securely
    return render(request, "platform/settings.html", {
        "active": "settings", "user_name": user.full_name, "user_email": user.email,
        "support_whatsapp": support_whatsapp or settings.SUPPORT_WHATSAPP,
        "success": "Secrets received. Wire encrypted persistence in deployment.",
    })


# ---------- Company auth ----------
@app.get("/company/login", response_class=HTMLResponse)
async def company_login_page(request: Request):
    return render(request, "auth/company_login.html", {"error": None, "brand": ""})


@app.post("/company/login")
async def company_login(
    request: Request, brand: str = Form(...), email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    brand_q = brand.strip().lower()
    company = (await db.execute(
        select(Company).where(or_(
            func.lower(Company.name) == brand_q,
            Company.slug == brand_q.replace(" ", "-"),
        ))
    )).scalar_one_or_none()
    if not company or company.status == "suspended":
        return render(request, "auth/company_login.html", {
            "error": "Company not found or suspended", "brand": brand,
        }, 400)
    user = (await db.execute(
        select(User).where(User.email == email.strip().lower(), User.company_id == company.id)
    )).scalar_one_or_none()
    if not user or user.role not in (UserRole.COMPANY_ADMIN, UserRole.COMPANY_STAFF):
        return render(request, "auth/company_login.html", {
            "error": "Invalid email or password for this company", "brand": brand,
        }, 400)
    if not verify_password(password, user.hashed_password):
        return render(request, "auth/company_login.html", {
            "error": "Invalid email or password for this company", "brand": brand,
        }, 400)
    resp = RedirectResponse("/company/dashboard", status_code=303)
    return set_session(resp, user.id)


@app.get("/company/logout")
async def company_logout():
    return clear_session(RedirectResponse("/company/login", status_code=303))


# ---------- Company pages ----------
async def company_ctx(user: User, db: AsyncSession):
    try:
        company = await db.get(Company, user.company_id)
        return company
    except Exception as e:
        print("company_ctx:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # Last resort: load by id with only core columns via text
        try:
            from sqlalchemy import text
            row = (await db.execute(text(
                "SELECT id, name, slug, currency, platform_note, status FROM companies WHERE id = :id"
            ), {"id": user.company_id})).mappings().first()
            if not row:
                return None
            c = Company()
            c.id = row["id"]
            c.name = row["name"]
            c.slug = row.get("slug")
            c.currency = row.get("currency") or "NGN"
            c.platform_note = row.get("platform_note")
            c.status = row.get("status") or "active"
            return c
        except Exception as e2:
            print("company_ctx fallback:", e2)
            try:
                await db.rollback()
            except Exception:
                pass
            return None


@app.get("/company/dashboard", response_class=HTMLResponse)
# DASHBOARD_FIX_V2_20260927
async def company_dashboard(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Always returns 200 HTML for company dashboard."""
    ctx = {
        "active": "dashboard",
        "company_name": "Company",
        "user_name": getattr(user, "full_name", None) or "Admin",
        "currency": "NGN",
        "orders_count": 0,
        "pending_count": 0,
        "chats_count": 0,
        "recent_orders": [],
        "platform_note": None,
        "revenue_daily": 0,
        "revenue_weekly": 0,
        "revenue_monthly": 0,
    }
    try:
        company = await company_ctx(user, db)
        if company:
            ctx["company_name"] = getattr(company, "name", None) or "Company"
            ctx["currency"] = getattr(company, "currency", None) or "NGN"
            ctx["platform_note"] = getattr(company, "platform_note", None)
            cid = company.id
            try:
                ctx["orders_count"] = int((await db.execute(
                    select(func.count()).select_from(Order).where(Order.company_id == cid)
                )).scalar() or 0)
            except Exception as e:
                print("dash oc", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            try:
                ctx["pending_count"] = int((await db.execute(
                    select(func.count()).select_from(Order).where(
                        Order.company_id == cid,
                        Order.status == OrderStatus.PAYMENT_SUBMITTED,
                    )
                )).scalar() or 0)
            except Exception as e:
                print("dash pending", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            try:
                ctx["chats_count"] = int((await db.execute(
                    select(func.count()).select_from(Conversation).where(Conversation.company_id == cid)
                )).scalar() or 0)
            except Exception as e:
                print("dash chats", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            try:
                recent = list((await db.execute(
                    select(Order).where(Order.company_id == cid).order_by(Order.created_at.desc()).limit(10)
                )).scalars().all())
                rows = []
                for o in recent:
                    try:
                        amt = getattr(o, "total", None)
                        if amt is None:
                            amt = getattr(o, "total_amount", 0) or 0
                        cur = getattr(o, "currency", None) or ctx["currency"]
                        cust = getattr(o, "customer_name", None) or getattr(o, "customer_wa_id", None) or "—"
                        st = getattr(o, "status", None)
                        st = st.value if hasattr(st, "value") else str(st or "")
                        # sanitize status for CSS class
                        st_safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in st)[:40]
                        rows.append({
                            "id": o.id,
                            "customer": cust,
                            "service": getattr(o, "service_name", None) or "—",
                            "status": st_safe,
                            "total": f"{cur} {float(amt or 0):,.0f}",
                        })
                    except Exception:
                        continue
                ctx["recent_orders"] = rows
            except Exception as e:
                print("dash recent", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
            try:
                rev = await revenue_stats(db, cid)
                ctx["revenue_daily"] = float(rev.get("daily") or 0)
                ctx["revenue_weekly"] = float(rev.get("weekly") or 0)
                ctx["revenue_monthly"] = float(rev.get("monthly") or 0)
            except Exception as e:
                print("dash rev", e)
                try:
                    await db.rollback()
                except Exception:
                    pass
    except Exception as e:
        print("dash outer", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
    try:
        return render(request, "company/dashboard.html", ctx)
    except Exception as e:
        print("dash template", type(e).__name__, e)
        # Absolute last resort: plain HTML, no Jinja layout
        return HTMLResponse(
            f"""<!DOCTYPE html><html><body style="font-family:system-ui;padding:1.5rem">
            <h1>{ctx.get('company_name','Company')} Dashboard</h1>
            <p>Welcome, {ctx.get('user_name','Admin')}</p>
            <p>Orders: {ctx.get('orders_count',0)} · Pending: {ctx.get('pending_count',0)} · Chats: {ctx.get('chats_count',0)}</p>
            <p>Revenue today: {ctx.get('currency','NGN')} {ctx.get('revenue_daily',0)}</p>
            <p><a href="/company/services">Services</a> · <a href="/company/orders">Orders</a> · <a href="/company/messages">Messages</a></p>
            </body></html>""",
            status_code=200,
        )


@app.get("/company/services", response_class=HTMLResponse)
async def company_services(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.data.master_catalog import MASTER_CATALOG, CATEGORIES, catalog_key
    company = await company_ctx(user, db)
    services = list((await db.execute(
        select(Service).where(Service.company_id == company.id).order_by(Service.category, Service.name)
    )).scalars().all())
    enabled_keys = {s.catalog_key for s in services if s.catalog_key}
    enabled_names = {(s.category, s.name) for s in services}
    rows = []
    for s in services:
        rows.append({
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "price": f"{company.currency} {s.base_price:,.0f} / {s.unit}",
            "base_price": s.base_price,
            "unit": s.unit,
            "pricing_method": getattr(s, "pricing_method", None) or "piece",
            "active": s.is_active,
            "description": s.description or "",
        })
    catalog_view = []
    for item in MASTER_CATALOG:
        key = catalog_key(item["category"], item["name"])
        on = key in enabled_keys or (item["category"], item["name"]) in enabled_names
        catalog_view.append({**item, "key": key, "enabled": on})
    q = (request.query_params.get("q") or "").strip().lower()
    cat_filter = (request.query_params.get("cat") or "").strip()
    if q:
        catalog_view = [c for c in catalog_view if q in c["name"].lower() or q in c["category"].lower()]
    if cat_filter:
        catalog_view = [c for c in catalog_view if c["category"] == cat_filter]
    return render(request, "company/services.html", {
        "active": "services", "company_name": company.name, "user_name": user.full_name,
        "services": rows, "catalog": catalog_view, "categories": CATEGORIES,
        "currency": company.currency, "q": request.query_params.get("q") or "",
        "cat": cat_filter, "saved": request.query_params.get("saved"),
    })


@app.post("/company/services/add")
async def company_services_add(
    request: Request,
    category: str = Form(...), name: str = Form(...), description: Optional[str] = Form(None),
    base_price: float = Form(0), unit: str = Form("per piece"),
    pricing_method: str = Form("piece"),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.data.master_catalog import catalog_key
    db.add(Service(
        company_id=user.company_id, category=category, name=name.strip(),
        description=description, base_price=base_price, unit=unit,
        pricing_method=pricing_method or "piece",
        catalog_key=catalog_key(category, name),
        is_active=True,
    ))
    await db.commit()
    return RedirectResponse("/company/services?saved=1", status_code=303)


@app.post("/company/services/enable")
async def company_services_enable(
    catalog_key: str = Form(...),
    category: str = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    pricing_method: str = Form("piece"),
    unit: str = Form("per piece"),
    base_price: float = Form(0),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    existing = (await db.execute(
        select(Service).where(
            Service.company_id == user.company_id,
            Service.catalog_key == catalog_key,
        )
    )).scalars().first()
    if existing:
        existing.is_active = True
        existing.base_price = base_price
        existing.unit = unit
        existing.pricing_method = pricing_method
        if description:
            existing.description = description
    else:
        flow = "generic"
        if "frame" in name.lower() or pricing_method == "fixed_size":
            flow = "frame"
        elif "nylon" in name.lower() or "poly bag" in name.lower():
            flow = "nylon"
        elif any(x in name.lower() for x in ("banner", "sav", "flex", "window")):
            flow = "large_format"
        elif any(x in name.lower() for x in ("shirt", "hoodie", "embroidery", "dtf", "garment")):
            flow = "garment"
        svc = Service(
            company_id=user.company_id,
            category=category,
            name=name.strip(),
            description=description,
            base_price=base_price,
            unit=unit,
            pricing_method=pricing_method if pricing_method != "fixed_size" else "fixed_size",
            catalog_key=catalog_key,
            flow_type=flow,
            is_active=True,
        )
        # force fixed_size when template exists
        from app.data.size_templates import SIZE_TEMPLATES
        from app.data.master_catalog import catalog_key as ck
        key = catalog_key
        if key in SIZE_TEMPLATES or any(k.endswith(name.lower().replace(" ", "-")[:20]) for k in SIZE_TEMPLATES):
            svc.pricing_method = "fixed_size"
            if "frame" in name.lower():
                svc.flow_type = "frame"
        db.add(svc)
        await db.flush()
        templates = SIZE_TEMPLATES.get(key) or []
        if not templates:
            # fuzzy match
            for tk, rows in SIZE_TEMPLATES.items():
                if name.lower() in tk or tk.split(":")[-1].replace("-", " ") in name.lower():
                    templates = rows
                    break
        for i, (label, price) in enumerate(templates):
            db.add(ServiceVariant(
                service_id=svc.id,
                label=label,
                subtitle="inches" if "x" in label.lower() else None,
                price=float(price or base_price or 0),
                sort_order=i,
                is_active=True,
            ))
    await db.commit()
    return RedirectResponse("/company/services?saved=1", status_code=303)


@app.post("/company/services/{service_id}/update")
async def company_services_update(
    service_id: int,
    base_price: float = Form(0),
    unit: str = Form("per piece"),
    pricing_method: str = Form("piece"),
    description: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    if not s or s.company_id != user.company_id:
        raise HTTPException(404)
    s.base_price = base_price
    s.unit = unit
    s.pricing_method = pricing_method
    if description is not None:
        s.description = description
    s.is_active = is_active is not None
    await db.commit()
    return RedirectResponse("/company/services?saved=1", status_code=303)


@app.post("/company/services/{service_id}/remove")
async def company_services_remove(
    service_id: int,
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    if s and s.company_id == user.company_id:
        await db.delete(s)
        await db.commit()
    return RedirectResponse("/company/services?saved=1", status_code=303)



@app.get("/company/services/{service_id}/variants", response_class=HTMLResponse)
async def company_service_variants(
    service_id: int, request: Request,
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    s = await db.get(Service, service_id)
    if not s or s.company_id != company.id:
        raise HTTPException(404)
    variants = list((await db.execute(
        select(ServiceVariant).where(ServiceVariant.service_id == s.id).order_by(ServiceVariant.sort_order, ServiceVariant.id)
    )).scalars().all())
    return render(request, "company/service_variants.html", {
        "active": "services", "company_name": company.name, "user_name": user.full_name,
        "service": s, "variants": variants, "currency": company.currency,
        "saved": request.query_params.get("saved"),
    })


@app.post("/company/services/{service_id}/variants/add")
async def company_variant_add(
    service_id: int, label: str = Form(...), price: float = Form(0),
    subtitle: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    if not s or s.company_id != user.company_id:
        raise HTTPException(404)
    count = len((await db.execute(select(ServiceVariant).where(ServiceVariant.service_id == s.id))).scalars().all())
    db.add(ServiceVariant(service_id=s.id, label=label.strip(), subtitle=subtitle, price=price, sort_order=count, is_active=True))
    s.pricing_method = "fixed_size"
    await db.commit()
    return RedirectResponse(f"/company/services/{service_id}/variants?saved=1", status_code=303)


@app.post("/company/services/{service_id}/variants/{variant_id}/update")
async def company_variant_update(
    service_id: int, variant_id: int,
    label: str = Form(...), price: float = Form(0),
    is_active: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    v = await db.get(ServiceVariant, variant_id)
    if not s or s.company_id != user.company_id or not v or v.service_id != s.id:
        raise HTTPException(404)
    v.label = label.strip()
    v.price = price
    v.is_active = is_active is not None
    await db.commit()
    return RedirectResponse(f"/company/services/{service_id}/variants?saved=1", status_code=303)


@app.post("/company/services/{service_id}/variants/{variant_id}/delete")
async def company_variant_delete(
    service_id: int, variant_id: int,
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    s = await db.get(Service, service_id)
    v = await db.get(ServiceVariant, variant_id)
    if s and s.company_id == user.company_id and v and v.service_id == s.id:
        await db.delete(v)
        await db.commit()
    return RedirectResponse(f"/company/services/{service_id}/variants?saved=1", status_code=303)



@app.get("/company/orders", response_class=HTMLResponse)
async def company_orders(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    rows = []
    try:
        orders = list((await db.execute(
            select(Order).where(Order.company_id == company.id).order_by(Order.created_at.desc())
        )).scalars().all())
        for o in orders:
            amt = getattr(o, "total", None)
            if amt is None:
                amt = getattr(o, "total_amount", 0) or 0
            cur = getattr(o, "currency", None) or company.currency or "NGN"
            st = o.status.value if hasattr(o.status, "value") else str(o.status)
            sl = str(st).lower()
            rows.append({
                "id": o.id,
                "customer": getattr(o, "customer_name", None) or getattr(o, "customer_wa_id", None) or "—",
                "service": o.service_name or "—",
                "status": sl.replace("_", " "),
                "status_class": "pending" if ("await" in sl or "payment" in sl) else "active",
                "total": f"{cur} {float(amt):,.0f}",
            })
    except Exception as e:
        print("orders query:", type(e).__name__, e)
    return render(request, "company/orders.html", {
        "active": "orders", "company_name": company.name, "user_name": user.full_name, "orders": rows,
    })


@app.get("/company/orders/{order_id}", response_class=HTMLResponse)
async def company_order_detail(
    order_id: int, request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    order = await db.get(Order, order_id)
    if not order or order.company_id != company.id:
        raise HTTPException(404)
    return render(request, "company/order_detail.html", {
        "active": "orders", "company_name": company.name, "user_name": user.full_name,
        "order_id": order.id, "customer": (getattr(order, "customer_name", None) or getattr(order, "customer_wa_id", None) or "—"),
        "service": order.service_name, "total": f"{getattr(order, 'currency', None) or company.currency} {float(getattr(order, 'total', None) if getattr(order, 'total', None) is not None else getattr(order, 'total_amount', 0) or 0):,.0f}",
        "status": order.status.value, "payment_proof": getattr(order, "payment_proof", None) or getattr(order, "payment_proof_url", None),
    })


@app.post("/company/orders/{order_id}/status")
async def company_order_status(
    order_id: int, status: str = Form(...), note: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    order = await db.get(Order, order_id)
    if not order or order.company_id != user.company_id:
        raise HTTPException(404)
    try:
        order.status = OrderStatus(status)
    except ValueError:
        pass
    order.status_note = note
    await db.commit()
    try:
        from app.services.bot_engine import notify_order_status
        await notify_order_status(db, order, status, note)
    except Exception:
        pass
    return RedirectResponse(f"/company/orders/{order_id}", status_code=303)


@app.get("/company/payments", response_class=HTMLResponse)
async def company_payments(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    accounts = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == company.id))).scalars().all()
    return render(request, "company/payments.html", {
        "active": "payments", "company_name": company.name, "user_name": user.full_name, "accounts": accounts,
    })


@app.post("/company/payments/add")
async def company_payments_add(
    bank_name: str = Form(...), account_name: str = Form(...), account_number: str = Form(...),
    instructions: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    existing = (await db.execute(select(PaymentDetail).where(PaymentDetail.company_id == user.company_id))).scalars().first()
    db.add(PaymentDetail(
        company_id=user.company_id, bank_name=bank_name, account_name=account_name,
        account_number=account_number, instructions=instructions, is_primary=existing is None,
    ))
    await db.commit()
    return RedirectResponse("/company/payments?saved=1", status_code=303)

@app.post("/company/payments/{pay_id}/update")
async def company_payments_update(
    pay_id: int,
    bank_name: str = Form(...), account_name: str = Form(...), account_number: str = Form(...),
    instructions: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    row = await db.get(PaymentDetail, pay_id)
    if not row or row.company_id != user.company_id:
        raise HTTPException(404)
    row.bank_name = bank_name.strip()
    row.account_name = account_name.strip()
    row.account_number = account_number.strip()
    row.instructions = instructions
    await db.commit()
    return RedirectResponse("/company/payments?saved=1", status_code=303)


@app.post("/company/payments/{pay_id}/delete")
async def company_payments_delete(
    pay_id: int,
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    row = await db.get(PaymentDetail, pay_id)
    if row and row.company_id == user.company_id:
        await db.delete(row)
        await db.commit()
    return RedirectResponse("/company/payments?saved=1", status_code=303)


@app.get("/company/bot", response_class=HTMLResponse)
async def company_bot_alias(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    return await company_bot(request, user, db)


@app.get("/company/messages", response_class=HTMLResponse)
async def company_messages(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    from app.models.conversation import Conversation, Message
    company = await company_ctx(user, db)
    convs = (await db.execute(
        select(Conversation).where(Conversation.company_id == company.id).order_by(Conversation.updated_at.desc())
    )).scalars().all()
    selected_id = request.query_params.get("c")
    messages = []
    selected = None
    if selected_id:
        try:
            cid = int(selected_id)
        except ValueError:
            cid = None
        if cid:
            selected = await db.get(Conversation, cid)
            if selected and selected.company_id == company.id:
                messages = list((await db.execute(
                    select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.asc())
                )).scalars().all())
    threads = []
    for c in convs:
        last = (await db.execute(
            select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at.desc()).limit(1)
        )).scalars().first()
        threads.append({
            "id": c.id,
            "wa": c.customer_wa_id,
            "name": c.customer_name or c.customer_wa_id,
            "needs_human": bool(getattr(c, "needs_human", False) or getattr(c, "is_live_takeover", False)),
            "preview": (last.body or "")[:80] if last else "—",
            "state": c.state,
        })
    return render(request, "company/messages.html", {
        "active": "messages",
        "company_name": company.name,
        "user_name": user.full_name,
        "threads": threads,
        "messages": messages,
        "selected": selected,
        "selected_id": int(selected_id) if selected_id and str(selected_id).isdigit() else None,
    })



@app.post("/company/messages/reply")
async def company_messages_reply(
    conversation_id: int = Form(...),
    body: str = Form(""),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import Conversation, Message
    from app.models.company import CompanyWhatsAppNumber
    from app.services.whatsapp_send import send_text
    company = await company_ctx(user, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company.id:
        raise HTTPException(404)
    text = (body or "").strip()
    if not text:
        return RedirectResponse(f"/company/messages?c={conversation_id}", status_code=303)
    # mark staff takeover so bot does not auto-reply next customer msg until cleared - optional soft flag
    conv.is_live_takeover = True
    db.add(Message(conversation_id=conv.id, direction="outbound", body=text))
    await db.commit()
    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.company_id == company.id,
            CompanyWhatsAppNumber.is_active == True,
        )
    )).scalars().first()
    if link and link.access_token:
        await send_text(link.phone_number_id, link.access_token, conv.customer_wa_id, text)
    return RedirectResponse(f"/company/messages?c={conversation_id}&sent=1", status_code=303)


@app.post("/company/messages/release")
async def company_messages_release(
    conversation_id: int = Form(...),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import Conversation
    conv = await db.get(Conversation, conversation_id)
    if conv and conv.company_id == user.company_id:
        conv.is_live_takeover = False
        conv.needs_human = False
        conv.handoff_reason = None
        await db.commit()
    return RedirectResponse(f"/company/messages?c={conversation_id}", status_code=303)


@app.get("/company/chats", response_class=HTMLResponse)
async def company_chats(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    company = await company_ctx(user, db)
    return render(request, "company/chats.html", {
        "active": "chats", "company_name": company.name, "user_name": user.full_name,
    })


@app.get("/company/bot-settings", response_class=HTMLResponse)
async def company_bot(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    import json
    company = await company_ctx(user, db)
    flags = {}
    try:
        flags = json.loads(getattr(company, "bot_flags", None) or "{}")
    except Exception:
        flags = {}
    return render(request, "company/bot_settings.html", {
        "active": "bot", "company_name": company.name, "user_name": user.full_name,
        "greeting": company.greeting_message or "", "language": company.bot_language or "both",
        "currency": company.currency,
        "ask_size_help": flags.get("ask_size_help", True),
        "ask_payment_proof": flags.get("ask_payment_proof", True),
        "ask_delivery": flags.get("ask_delivery", True),
        "calc_delivery_fee": flags.get("calc_delivery_fee", False),
        "offer_pidgin": flags.get("offer_pidgin", True),
        "design_fee_default": getattr(company, "design_fee_default", 0) or 0,
        "custom_ai_instructions": getattr(company, "custom_ai_instructions", None) or "",
        "enquiry_whatsapp": getattr(company, "enquiry_whatsapp", None) or "",
        "enquiry_phone": getattr(company, "enquiry_phone", None) or "",
        "enquiry_note": getattr(company, "enquiry_note", None) or "",
        "bot_personality": getattr(company, "bot_personality", None) or "friendly",
        "business_hours": getattr(company, "business_hours", None) or "",
        "about_text": getattr(company, "about_text", None) or "",
        "location_text": getattr(company, "location_text", None) or "",
        "saved": request.query_params.get("saved"),
    })


@app.post("/company/bot-settings")
async def company_bot_save(
    request: Request,
    greeting: Optional[str] = Form(None), language: str = Form("both"), currency: str = Form("NGN"),
    design_fee_default: float = Form(0),
    custom_ai_instructions: Optional[str] = Form(None),
    bot_personality: str = Form("friendly"),
    business_hours: Optional[str] = Form(None),
    about_text: Optional[str] = Form(None),
    location_text: Optional[str] = Form(None),
    enquiry_whatsapp: Optional[str] = Form(None),
    enquiry_phone: Optional[str] = Form(None),
    enquiry_note: Optional[str] = Form(None),

    ask_size_help: Optional[str] = Form(None),
    ask_payment_proof: Optional[str] = Form(None),
    ask_delivery: Optional[str] = Form(None),
    calc_delivery_fee: Optional[str] = Form(None),
    offer_pidgin: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    import json
    company = await company_ctx(user, db)
    if greeting is not None:
        company.greeting_message = greeting
    company.bot_language = language
    company.currency = currency
    company.design_fee_default = float(design_fee_default or 0)
    company.custom_ai_instructions = (custom_ai_instructions or "").strip() or None
    company.enquiry_whatsapp = (enquiry_whatsapp or "").strip() or None
    company.enquiry_phone = (enquiry_phone or "").strip() or None
    company.enquiry_note = (enquiry_note or "").strip() or None
    company.bot_personality = bot_personality or "friendly"
    company.business_hours = (business_hours or "").strip() or None
    company.about_text = (about_text or "").strip() or None
    company.location_text = (location_text or "").strip() or None
    flags = {
        "ask_size_help": ask_size_help is not None,
        "ask_payment_proof": ask_payment_proof is not None,
        "ask_delivery": ask_delivery is not None,
        "calc_delivery_fee": calc_delivery_fee is not None,
        "offer_pidgin": offer_pidgin is not None,
    }
    company.bot_flags = json.dumps(flags)
    await db.commit()
    return RedirectResponse("/company/bot-settings?saved=1", status_code=303)



@app.post("/company/whatsapp")
async def company_wa_save(
    request: Request,
    phone_number_id: str = Form(...),
    access_token: Optional[str] = Form(None),
    display_number: Optional[str] = Form(None),
    waba_id: Optional[str] = Form(None),
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.models.company import CompanyWhatsAppNumber
    company = await company_ctx(user, db)
    pid = (phone_number_id or "").strip()
    if not pid.isdigit():
        return RedirectResponse("/company/whatsapp?error=phone_id", status_code=303)
    waba = (waba_id or "").strip() or None
    if waba and not waba.isdigit():
        return RedirectResponse("/company/whatsapp?error=waba", status_code=303)
    token = (access_token or "").strip()
    disp = (display_number or "").strip() or None

    existing = (await db.execute(
        select(CompanyWhatsAppNumber).where(CompanyWhatsAppNumber.company_id == company.id)
    )).scalars().first()
    if existing:
        existing.phone_number_id = pid
        existing.waba_id = waba
        existing.display_number = disp
        if token:
            existing.access_token = token
        existing.is_active = True
        if not existing.access_token:
            return RedirectResponse("/company/whatsapp?error=token", status_code=303)
    else:
        if not token:
            return RedirectResponse("/company/whatsapp?error=token", status_code=303)
        db.add(CompanyWhatsAppNumber(
            company_id=company.id,
            phone_number_id=pid,
            waba_id=waba,
            access_token=token,
            display_number=disp,
            is_active=True,
        ))
    await db.commit()
    return RedirectResponse("/company/whatsapp?saved=1", status_code=303)


@app.get("/company/whatsapp", response_class=HTMLResponse)
async def company_wa(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    from app.models.company import CompanyWhatsAppNumber
    company = await company_ctx(user, db)
    wa = (await db.execute(
        select(CompanyWhatsAppNumber).where(CompanyWhatsAppNumber.company_id == company.id)
    )).scalars().first()
    saved = request.query_params.get("saved")
    err = request.query_params.get("error")
    error_msg = None
    if err == "phone_id":
        error_msg = "Phone Number ID must be digits only (from Meta API Setup)."
    elif err == "waba":
        error_msg = "WhatsApp Business Account ID must be digits only (not an email)."
    elif err == "token":
        error_msg = "Access token is required for the first connection."
    return render(request, "company/whatsapp.html", {
        "active": "whatsapp", "company_name": company.name, "user_name": user.full_name,
        "wa": wa, "saved": saved, "error": error_msg,
    })


@app.get("/company/settings", response_class=HTMLResponse)
async def company_settings(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    company = await company_ctx(user, db)
    return render(request, "company/settings.html", {
        "active": "settings",
        "company_name": company.name,
        "user_name": user.full_name,
        "user_email": user.email,
        "success": None,
        "error": None,
    })


@app.post("/company/settings")
async def company_settings_save(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    company_display_name: Optional[str] = Form(None),
    current_password: Optional[str] = Form(None),
    new_password: Optional[str] = Form(None),
    confirm_password: Optional[str] = Form(None),
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    user.full_name = full_name.strip()
    user.email = email.strip().lower()
    # Brand name in sidebar + bot context
    new_brand = (company_display_name or "").strip()
    if new_brand and new_brand != company.name:
        company.name = new_brand[:150]
    err = None
    if new_password:
        if new_password != (confirm_password or ""):
            err = "Passwords do not match"
        elif not current_password or not verify_password(current_password, user.hashed_password):
            err = "Current password incorrect"
        else:
            user.hashed_password = hash_password(new_password)
    if not err:
        await db.commit()
        await db.refresh(company)
    return render(request, "company/settings.html", {
        "active": "settings",
        "company_name": company.name,
        "user_name": user.full_name,
        "user_email": user.email,
        "success": None if err else "Saved. Sidebar and bot now use your company display name.",
        "error": err,
    })


# ---------- WhatsApp webhook stubs ----------
@app.get("/api/webhook/whatsapp")
async def wa_verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == settings.WHATSAPP_VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Verification failed")


@app.post("/api/webhook/whatsapp")
async def wa_incoming(request: Request, background_tasks: BackgroundTasks):
    """ACK Meta immediately; process AI in background (spec §41)."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}
    # Schedule background processing — do not await Grok here
    background_tasks.add_task(_run_wa_background, body)
    return {"status": "ok"}


async def _run_wa_background(body: dict):
    from app.services.webhook_worker import process_whatsapp_payload
    try:
        await process_whatsapp_payload(body)
    except Exception as e:
        print("bg_wa_error", type(e).__name__, e)





@app.get("/company/broadcast", response_class=HTMLResponse)
async def company_broadcast_page(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    past = list((await db.execute(
        select(Broadcast).where(Broadcast.company_id == company.id).order_by(Broadcast.id.desc()).limit(20)
    )).scalars().all())
    cust_count = len((await db.execute(
        select(Conversation.customer_wa_id).where(Conversation.company_id == company.id).distinct()
    )).scalars().all())
    return render(request, "company/broadcast.html", {
        "active": "broadcast", "company_name": company.name, "user_name": user.full_name,
        "past": past, "customer_count": cust_count, "saved": request.query_params.get("saved"),
        "sent": request.query_params.get("sent"),
    })


@app.post("/company/broadcast/send")
async def company_broadcast_send(
    message: str = Form(...),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.models.company import CompanyWhatsAppNumber
    from app.services.whatsapp_send import send_text
    company = await company_ctx(user, db)
    text = (message or "").strip()
    if not text:
        return RedirectResponse("/company/broadcast", status_code=303)
    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.company_id == company.id,
            CompanyWhatsAppNumber.is_active == True,
        )
    )).scalars().first()
    recipients = list((await db.execute(
        select(Conversation.customer_wa_id).where(Conversation.company_id == company.id).distinct()
    )).scalars().all())
    ok = fail = 0
    if link and link.access_token:
        for wa in recipients:
            try:
                await send_text(link.phone_number_id, link.access_token, wa, text)
                ok += 1
            except Exception:
                fail += 1
    else:
        fail = len(recipients)
    db.add(Broadcast(
        company_id=company.id, message=text,
        recipient_count=len(recipients), success_count=ok, fail_count=fail,
        created_by=user.full_name or user.email,
    ))
    await db.commit()
    return RedirectResponse(f"/company/broadcast?sent=1", status_code=303)


@app.get("/api/company/notifications")
async def company_notifications_api(
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    rows = list((await db.execute(
        select(AdminNotification).where(
            AdminNotification.company_id == user.company_id,
            AdminNotification.is_read == False,
        ).order_by(AdminNotification.id.desc()).limit(30)
    )).scalars().all())
    return [
        {
            "id": n.id,
            "title": n.title or "",
            "body": n.body or "",
            "priority": getattr(n, "priority", None) or ("high" if "attention" in ((n.title or "") + (n.body or "")).lower() else "normal"),
            "conversation_id": getattr(n, "conversation_id", None),
            "link": getattr(n, "link_path", None) or "/company/messages",
            "created_at": n.created_at.isoformat() if getattr(n, "created_at", None) else None,
        }
        for n in rows
    ]


@app.post("/api/company/notifications/{nid}/read")
async def company_notification_read(
    nid: int, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    n = await db.get(AdminNotification, nid)
    if n and n.company_id == user.company_id:
        n.is_read = True
        await db.commit()
    return {"ok": True}



@app.get("/company/revenue/pdf")
async def company_revenue_pdf(
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Statement download — no lazy ORM IO (avoids MissingGreenlet on async SQLAlchemy)."""
    from fastapi.responses import Response
    from datetime import datetime, timezone

    company = await company_ctx(user, db)
    if not company:
        raise HTTPException(404, "Company not found")

    # Snapshot company fields NOW as plain Python (no later ORM access)
    company_id = int(company.id)
    company_name = str(getattr(company, "name", None) or "Company")
    company_slug = str(getattr(company, "slug", None) or company_id)
    cur = str(getattr(company, "currency", None) or "NGN")

    rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    try:
        rev = await revenue_stats(db, company_id)
    except Exception as e:
        print("revenue_pdf stats:", e)
        try:
            await db.rollback()
        except Exception:
            pass

    rows = []
    try:
        paid = [
            OrderStatus.PAYMENT_CONFIRMED,
            OrderStatus.IN_PRODUCTION,
            OrderStatus.READY,
            OrderStatus.COMPLETED,
        ]
        result = await db.execute(
            select(Order).where(
                Order.company_id == company_id,
                Order.status.in_(paid),
            ).order_by(Order.created_at.desc()).limit(200)
        )
        orders = list(result.scalars().all())
        for o in orders:
            # Materialize every field while still in async context
            st = getattr(o, "status", None)
            if hasattr(st, "value"):
                st = st.value
            else:
                st = str(st or "")
            created = getattr(o, "created_at", None)
            dt_s = created.strftime("%Y-%m-%d") if created is not None else "—"
            amt = getattr(o, "total", None)
            if amt is None:
                amt = getattr(o, "total_amount", 0) or 0
            cust = getattr(o, "customer_name", None) or getattr(o, "customer_wa_id", None) or "—"
            svc = getattr(o, "service_name", None) or "—"
            rows.append({
                "id": int(o.id),
                "date": dt_s,
                "customer": str(cust)[:20],
                "service": str(svc)[:22],
                "amount": float(amt or 0),
                "status": str(st),
            })
    except Exception as e:
        print("revenue_pdf orders:", e)
        try:
            await db.rollback()
        except Exception:
            pass

    # From here: pure Python only — safe for reportlab sync code
    lines = [
        "CLIENT-RaQ REVENUE STATEMENT",
        f"Company: {company_name}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "SUMMARY",
        f"  Today:   {cur} {float(rev.get('daily') or 0):,.2f}",
        f"  Week:    {cur} {float(rev.get('weekly') or 0):,.2f}",
        f"  Month:   {cur} {float(rev.get('monthly') or 0):,.2f}",
        "",
        "CONFIRMED ORDERS (payment confirmed and later)",
        f"{'ID':>6}  {'Date':12}  {'Customer':22}  {'Service':24}  {'Amount':>14}  Status",
        "-" * 90,
    ]
    for r in rows:
        lines.append(
            f"{r['id']:6d}  {r['date']:12}  {r['customer']:22}  {r['service']:24}  "
            f"{cur} {r['amount']:>10,.2f}  {r['status']}"
        )
    if not rows:
        lines.append("(No confirmed paid orders yet)")
    lines.append("")
    lines.append("Amounts are from orders marked payment_confirmed or later.")
    body = "\n".join(lines)
    fname_base = f"revenue-{company_slug}"

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        import io

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        y = h - 20 * mm
        c.setFont("Helvetica-Bold", 14)
        c.drawString(20 * mm, y, "Client-RaQ Revenue Statement")
        y -= 8 * mm
        c.setFont("Helvetica", 9)
        for line in lines[1:]:
            if y < 15 * mm:
                c.showPage()
                c.setFont("Helvetica", 9)
                y = h - 20 * mm
            # reportlab needs latin-1 safe-ish strings
            safe = line[:110].encode("latin-1", "replace").decode("latin-1")
            c.drawString(15 * mm, y, safe)
            y -= 5 * mm
        c.save()
        return Response(
            content=buf.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.pdf"'},
        )
    except Exception as e:
        print("revenue_pdf reportlab:", e)
        return Response(
            content=body.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.txt"'},
        )


@app.get("/health")
async def health():
    return {"status": "ok", "app": "Client-RaQ"}
