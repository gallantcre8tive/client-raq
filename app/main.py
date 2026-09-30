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
from app.models.service_image import ServiceExampleImage  # noqa: F401
from app.models.conversation import AdminNotification, Broadcast, Customer, Conversation, Message, Order
from app.models.platform_message import PlatformMessage
from app.models.conversation import Order, Conversation, OrderStatus
from app.core.security import hash_password, verify_password, create_access_token
from app.core.deps import get_current_user, require_platform, require_company

settings = get_settings()
BASE = Path(__file__).resolve().parent
app = FastAPI(title="Client-RaQ")

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Prefer redirects for browser auth errors on company/platform pages."""
    path = request.url.path or ""
    accept = (request.headers.get("accept") or "")
    wants_html = "text/html" in accept
    if wants_html and exc.status_code in (401, 403):
        if path.startswith("/company"):
            # Platform cookie on company routes → company login
            return RedirectResponse("/company/login?error=auth", status_code=303)
        if path.startswith("/platform"):
            return RedirectResponse("/platform/login?error=auth", status_code=303)
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)



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
# Serve WhatsApp / admin uploads (MEDIA_ROOT, default "uploads")
from app.config import get_settings as _gs
_media_root = Path(_gs().MEDIA_ROOT)
if not _media_root.is_absolute():
    _media_root = (BASE / _media_root).resolve()
_media_root.mkdir(parents=True, exist_ok=True)
# also keep legacy folder
(BASE.parent / "media").mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_media_root)), name="media")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    try:
        return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)
    except TypeError:
        # Older Starlette/Jinja2 API
        return templates.TemplateResponse(name, ctx, status_code=status_code)








@app.on_event("startup")
async def startup():
    """Do not block port binding. Schema init runs with a hard timeout in the background."""
    import asyncio

    async def _bg_init():
        try:
            await init_db()
            print("startup: db ready")
        except Exception as e:
            print(f"startup DB init warning: {type(e).__name__}: {e}")

    try:
        # Prefer non-blocking so Render detects open port immediately
        asyncio.create_task(_bg_init())
    except Exception as e:
        print("startup schedule:", e)
        try:
            await asyncio.wait_for(init_db(), timeout=20)
        except Exception as e2:
            print(f"startup fallback: {e2}")


# ---------- Public ----------


async def revenue_stats(db: AsyncSession, company_id: int | None = None) -> dict:
    """Sum confirmed revenue. Avoid asyncpg NULL-typed params; never crash dashboard."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())
    month0 = day0.replace(day=1)
    paid_statuses = (
        "PAYMENT_CONFIRMED", "IN_PRODUCTION", "READY", "COMPLETED",
        "payment_confirmed", "in_production", "ready", "completed",
    )

    async def _sum_since(since) -> float:
        try:
            if company_id is None:
                sql = text("""
                    SELECT COALESCE(SUM(COALESCE(total, total_amount, 0)), 0) AS s
                    FROM orders
                    WHERE created_at >= :since
                      AND status::text = ANY(:statuses)
                """)
                r = await db.execute(sql, {"since": since, "statuses": list(paid_statuses)})
            else:
                sql = text("""
                    SELECT COALESCE(SUM(COALESCE(total, total_amount, 0)), 0) AS s
                    FROM orders
                    WHERE created_at >= :since
                      AND company_id = :cid
                      AND status::text = ANY(:statuses)
                """)
                r = await db.execute(sql, {
                    "since": since,
                    "cid": int(company_id),
                    "statuses": list(paid_statuses),
                })
            return float(r.scalar() or 0)
        except Exception as e1:
            print("revenue sum primary:", type(e1).__name__, e1)
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                if company_id is None:
                    sql2 = text("""
                        SELECT COALESCE(SUM(COALESCE(total_amount, total, 0)), 0) AS s
                        FROM orders WHERE created_at >= :since
                    """)
                    r = await db.execute(sql2, {"since": since})
                else:
                    sql2 = text("""
                        SELECT COALESCE(SUM(COALESCE(total_amount, total, 0)), 0) AS s
                        FROM orders
                        WHERE created_at >= :since AND company_id = :cid
                    """)
                    r = await db.execute(sql2, {"since": since, "cid": int(company_id)})
                return float(r.scalar() or 0)
            except Exception as e2:
                print("revenue sum fallback:", type(e2).__name__, e2)
                try:
                    await db.rollback()
                except Exception:
                    pass
                return 0.0

    return {
        "daily": await _sum_since(day0),
        "weekly": await _sum_since(week0),
        "monthly": await _sum_since(month0),
    }




@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return render(request, "public/landing.html")


# ---------- Platform auth ----------

def set_session(response: RedirectResponse, user_id: int, scope: str = "company") -> RedirectResponse:
    """Set auth cookie. secure=True required for HTTPS (Render / clientraq.com)."""
    token = create_access_token({"sub": str(user_id), "scope": scope})
    common = dict(
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
        secure=True,  # clientraq.com is HTTPS — without this browser may drop cookie
    )
    try:
        plat = settings.SESSION_COOKIE_PLATFORM
        comp = settings.SESSION_COOKIE_COMPANY
    except Exception:
        plat, comp = "crq_platform_session", "crq_company_session"
    legacy = getattr(settings, "SESSION_COOKIE", "crq_session")
    if scope == "platform":
        response.set_cookie(plat, token, **common)
    else:
        response.set_cookie(comp, token, **common)
    response.set_cookie(legacy, token, **common)
    return response


def clear_session(response: RedirectResponse, scope: str | None = None) -> RedirectResponse:
    try:
        plat = settings.SESSION_COOKIE_PLATFORM
        comp = settings.SESSION_COOKIE_COMPANY
    except Exception:
        plat, comp = "crq_platform_session", "crq_company_session"
    legacy = getattr(settings, "SESSION_COOKIE", "crq_session")
    opts = dict(path="/")
    if scope in (None, "platform"):
        response.delete_cookie(plat, **opts)
    if scope in (None, "company"):
        response.delete_cookie(comp, **opts)
    response.delete_cookie(legacy, **opts)
    return response


@app.get("/platform/login", response_class=HTMLResponse)
async def platform_login_page(request: Request):
    return render(request, "auth/platform_login.html", {"error": None})


@app.post("/platform/login")
async def platform_login(
    request: Request, email: str = Form(...), password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email_q = email.strip().lower()
    user = None
    try:
        r = await db.execute(select(User).where(User.email == email_q))
        user = r.scalar_one_or_none()
    except Exception as e:
        print("platform_login orm:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # Raw SQL fallback if enum still broken on read
        from sqlalchemy import text
        row = (await db.execute(
            text("SELECT id, hashed_password, role::text AS role FROM users WHERE lower(email)=:e LIMIT 1"),
            {"e": email_q},
        )).mappings().first()
        if not row or not verify_password(password, row["hashed_password"]):
            return render(request, "auth/platform_login.html", {"error": "Invalid email or password"}, 400)
        role = str(row["role"] or "").lower()
        if "platform" not in role:
            return render(request, "auth/platform_login.html", {"error": "Invalid email or password"}, 400)
        resp = RedirectResponse("/platform/dashboard", status_code=303)
        return set_session(resp, int(row["id"]), scope="platform")

    def _is_platform(u) -> bool:
        r = getattr(u, "role", None)
        if r is None:
            return False
        if hasattr(r, "value"):
            return str(r.value).lower() == "platform_admin"
        s = str(r).lower()
        return "platform" in s

    if not user or not _is_platform(user) or not verify_password(password, user.hashed_password):
        return render(request, "auth/platform_login.html", {"error": "Invalid email or password"}, 400)
    resp = RedirectResponse("/platform/dashboard", status_code=303)
    return set_session(resp, user.id, scope="platform")


@app.get("/platform/logout")
async def platform_logout():
    return clear_session(RedirectResponse("/platform/login", status_code=303))


# ---------- Platform pages ----------
@app.get("/platform/dashboard", response_class=HTMLResponse)
async def platform_dashboard(
    request: Request, user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    """Platform admin home — all values materialized as plain Python (no lazy ORM IO)."""
    # Snapshot user while session is live (avoids MissingGreenlet in templates)
    user_name = str(getattr(user, "full_name", None) or "Platform Admin")

    company_rows: list[dict] = []
    cc = oc = vc = 0
    rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}

    # Schema repairs run on startup only — not on every dashboard hit (avoids hang)

    try:
        result = await db.execute(select(Company).order_by(Company.created_at.desc()).limit(20))
        companies = list(result.scalars().all())
        # Materialize immediately before any other query/rollback
        for c in companies:
            st = c.status
            if hasattr(st, "value"):
                st = st.value
            else:
                st = str(st or "active")
            created = getattr(c, "created_at", None)
            company_rows.append({
                "id": int(c.id),
                "name": str(c.name or ""),
                "slug": str(getattr(c, "slug", None) or ""),
                "country": str(getattr(c, "country", None) or "—"),
                "currency": str(getattr(c, "currency", None) or "NGN"),
                "status": str(st),
                "created_at": created.strftime("%Y-%m-%d") if created is not None else "—",
            })
        try:
            cc = int((await db.execute(select(func.count()).select_from(Company))).scalar() or 0)
        except Exception:
            cc = len(company_rows)
    except Exception as e:
        print("platform_dash companies:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass

    try:
        oc = int((await db.execute(select(func.count()).select_from(Order))).scalar() or 0)
    except Exception as e:
        print("platform_dash orders:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        oc = 0

    try:
        vc = int((await db.execute(select(func.count()).select_from(Conversation))).scalar() or 0)
    except Exception as e:
        print("platform_dash conv:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        vc = 0

    try:
        rev = await revenue_stats(db)
        if not isinstance(rev, dict):
            rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    except Exception as e:
        print("platform_dash rev:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        rev = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}

    try:
        return render(request, "platform/dashboard.html", {
            "active": "dashboard",
            "user_name": user_name,
            "companies_count": cc,
            "orders_count": oc,
            "conv_count": vc,
            "revenue": f'{float(rev.get("monthly") or 0):,.0f}',
            "revenue_daily": float(rev.get("daily") or 0),
            "revenue_weekly": float(rev.get("weekly") or 0),
            "revenue_monthly": float(rev.get("monthly") or 0),
            "companies": company_rows,
        })
    except Exception as e:
        print("platform_dash render:", type(e).__name__, e)
        # Last resort plain HTML so platform admin is never blocked
        rows_html = "".join(
            f"<tr><td>{r['name']}</td><td>{r['country']}</td><td>{r['currency']}</td><td>{r['status']}</td></tr>"
            for r in company_rows
        ) or "<tr><td colspan=4>No companies yet</td></tr>"
        return HTMLResponse(
            f"""<!DOCTYPE html><html><head><title>Platform Dashboard</title></head>
            <body style="font-family:system-ui;padding:1.5rem;background:#0b1220;color:#eee">
            <h1>Platform Dashboard</h1>
            <p>Welcome, {user_name}</p>
            <p>Companies: {cc} · Orders: {oc} · Chats: {vc}</p>
            <p>Revenue (month): {float(rev.get('monthly') or 0):,.0f}</p>
            <p><a href="/platform/companies" style="color:#6ea8fe">Companies</a> ·
               <a href="/platform/companies/new" style="color:#6ea8fe">Add company</a></p>
            <table border="1" cellpadding="8" style="border-collapse:collapse;margin-top:1rem">
            <tr><th>Name</th><th>Country</th><th>Currency</th><th>Status</th></tr>
            {rows_html}
            </table>
            </body></html>""",
            status_code=200,
        )




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
    companies = list((await db.execute(select(Company).order_by(Company.name))).scalars().all())
    # Prefer durable log
    notes = []
    try:
        rows = list((await db.execute(
            select(PlatformMessage).order_by(PlatformMessage.created_at.desc()).limit(50)
        )).scalars().all())
        for m in rows:
            created = getattr(m, "created_at", None)
            notes.append({
                "company": m.company_name or ("All companies" if not m.company_id else "—"),
                "message": m.body,
                "when": created.strftime("%d %b %Y · %H:%M") if created else "—",
            })
    except Exception as e:
        print("platform_messages list:", e)
        try:
            await db.rollback()
        except Exception:
            pass
        # fallback: last notifications titled platform
        try:
            rows = list((await db.execute(
                select(AdminNotification).where(
                    AdminNotification.title.ilike("%platform%")
                ).order_by(AdminNotification.id.desc()).limit(50)
            )).scalars().all())
            for n in rows:
                co = await db.get(Company, n.company_id) if n.company_id else None
                created = getattr(n, "created_at", None)
                notes.append({
                    "company": co.name if co else "—",
                    "message": n.body or "",
                    "when": created.strftime("%d %b %Y · %H:%M") if created else "—",
                })
        except Exception as e2:
            print("platform_messages fallback:", e2)

    return render(request, "platform/messages.html", {
        "active": "messages",
        "user_name": getattr(user, "full_name", None) or "Platform Admin",
        "companies": companies,
        "notes": notes,
        "sent": request.query_params.get("sent"),
    })




@app.post("/platform/messages")
async def platform_messages_send(
    company_id: str = Form(...), message: str = Form(...),
    user: User = Depends(require_platform), db: AsyncSession = Depends(get_db),
):
    """History + company notification. Uses multiple insert strategies so the bell always gets it."""
    from sqlalchemy import text as sa_text

    text_msg = (message or "").strip()
    if not text_msg:
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

    if not targets:
        return RedirectResponse("/platform/messages", status_code=303)

    sender = getattr(user, "full_name", None) or getattr(user, "email", None) or "Platform Admin"

    # Ensure notification table exists
    try:
        await db.execute(sa_text("""
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
    except Exception as e:
        print("ensure notif table:", e)
        try:
            await db.rollback()
        except Exception:
            pass

    for c in targets:
        cid = int(c.id)
        cname = str(c.name or "")
        # Insert notification — try several column sets
        inserted = False
        for sql, params in [
            ("""INSERT INTO admin_notifications
                (company_id, conversation_id, title, body, priority, is_read, link_path, created_at)
             VALUES (:cid, NULL, :title, :body, 'high', false, '/company/dashboard', NOW())""",
             {"cid": cid, "title": "Message from Client-RaQ Platform", "body": text_msg[:2000]}),
            ("""INSERT INTO admin_notifications
                (company_id, title, body, priority, is_read, created_at)
             VALUES (:cid, :title, :body, 'high', false, NOW())""",
             {"cid": cid, "title": "Message from Client-RaQ Platform", "body": text_msg[:2000]}),
            ("""INSERT INTO admin_notifications (company_id, title, body)
             VALUES (:cid, :title, :body)""",
             {"cid": cid, "title": "Message from Client-RaQ Platform", "body": text_msg[:2000]}),
        ]:
            if inserted:
                break
            try:
                await db.execute(sa_text(sql), params)
                await db.commit()
                inserted = True
                print("notif_inserted company_id=", cid)
            except Exception as e:
                print("notif_insert try fail:", type(e).__name__, e)
                try:
                    await db.rollback()
                except Exception:
                    pass

        # History log
        try:
            await db.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS platform_messages (
                    id SERIAL PRIMARY KEY,
                    company_id INTEGER,
                    company_name VARCHAR(200) DEFAULT '',
                    body TEXT NOT NULL,
                    sent_by VARCHAR(200),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await db.execute(sa_text("""
                INSERT INTO platform_messages (company_id, company_name, body, sent_by, created_at)
                VALUES (:cid, :cname, :body, :sender, NOW())
            """), {"cid": cid, "cname": cname, "body": text_msg[:4000], "sender": str(sender)[:200]})
            await db.commit()
        except Exception as e:
            print("platform_msg hist:", e)
            try:
                await db.rollback()
            except Exception:
                pass

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
    email_q = email.strip().lower()
    company = (await db.execute(
        select(Company).where(or_(
            func.lower(Company.name) == brand_q,
            Company.slug == brand_q.replace(" ", "-"),
        ))
    )).scalar_one_or_none()
    st = getattr(company, "status", None) if company else None
    if hasattr(st, "value"):
        st = st.value
    if not company or str(st or "").lower() == "suspended":
        return render(request, "auth/company_login.html", {
            "error": "Company not found or suspended", "brand": brand,
        }, 400)

    user = None
    try:
        user = (await db.execute(
            select(User).where(User.email == email_q, User.company_id == company.id)
        )).scalar_one_or_none()
    except Exception as e:
        print("company_login orm:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        from sqlalchemy import text
        row = (await db.execute(
            text("""
                SELECT id, hashed_password, role::text AS role, company_id
                FROM users WHERE lower(email)=:e AND company_id=:cid LIMIT 1
            """),
            {"e": email_q, "cid": company.id},
        )).mappings().first()
        if not row or not verify_password(password, row["hashed_password"]):
            return render(request, "auth/company_login.html", {
                "error": "Invalid email or password for this company", "brand": brand,
            }, 400)
        role = str(row["role"] or "").lower()
        if "company" not in role and "admin" not in role and "staff" not in role:
            return render(request, "auth/company_login.html", {
                "error": "Invalid email or password for this company", "brand": brand,
            }, 400)
        resp = RedirectResponse("/company/dashboard", status_code=303)
        return set_session(resp, int(row["id"]), scope="company")

    def _is_company_role(u) -> bool:
        r = getattr(u, "role", None)
        if r is None:
            return False
        if hasattr(r, "value"):
            v = str(r.value).lower()
        else:
            v = str(r).lower()
        return "company_admin" in v or "company_staff" in v or v.endswith("admin") or "staff" in v

    if not user or not _is_company_role(user) or not verify_password(password, user.hashed_password):
        return render(request, "auth/company_login.html", {
            "error": "Invalid email or password for this company", "brand": brand,
        }, 400)
    resp = RedirectResponse("/company/dashboard", status_code=303)
    return set_session(resp, user.id, scope="company")


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
            ctx["platform_note"] = None  # platform messages use notification bell only
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




@app.get("/company/services/{service_id}/examples", response_class=HTMLResponse)
async def company_service_examples(
    service_id: int,
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.models.service_image import ServiceExampleImage
    company = await company_ctx(user, db)
    s = await db.get(Service, service_id)
    if not s or s.company_id != company.id:
        raise HTTPException(404)
    imgs = list((await db.execute(
        select(ServiceExampleImage).where(
            ServiceExampleImage.service_id == service_id,
            ServiceExampleImage.company_id == company.id,
        ).order_by(ServiceExampleImage.id.desc())
    )).scalars().all())
    rows = []
    for im in imgs:
        rows.append({
            "id": im.id,
            "title": im.title or im.original_name or f"Image {im.id}",
            "description": im.description or "",
            "tags": im.tags or "",
            "src": f"/company/service-images/{im.id}",
            "active": im.is_active,
        })
    return render(request, "company/service_examples.html", {
        "active": "services",
        "company_name": company.name,
        "user_name": user.full_name,
        "service": s,
        "images": rows,
    })


@app.post("/company/services/{service_id}/examples/upload")
async def company_service_examples_upload(
    service_id: int,
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.models.service_image import ServiceExampleImage
    from app.services.attachment_serve import media_root
    import uuid
    from pathlib import Path as _P

    company = await company_ctx(user, db)
    s = await db.get(Service, service_id)
    if not s or s.company_id != company.id:
        raise HTTPException(404)
    form = await request.form()
    upload = form.get("file")
    title = (form.get("title") or "").strip() or None
    description = (form.get("description") or "").strip() or None
    tags = (form.get("tags") or "").strip() or None
    if not upload or not getattr(upload, "filename", None):
        return RedirectResponse(f"/company/services/{service_id}/examples", status_code=303)
    raw = await upload.read()
    if not raw:
        return RedirectResponse(f"/company/services/{service_id}/examples", status_code=303)
    ext = _P(upload.filename).suffix or ".jpg"
    fname = f"ex_{service_id}_{uuid.uuid4().hex[:10]}{ext}"
    dest_dir = media_root() / str(company.id) / "examples"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / fname).write_bytes(raw)
    rel = f"{company.id}/examples/{fname}"
    mime = getattr(upload, "content_type", None) or "image/jpeg"
    db.add(ServiceExampleImage(
        company_id=company.id,
        service_id=service_id,
        storage_path=rel,
        original_name=upload.filename,
        mime_type=mime,
        title=title,
        description=description,
        tags=tags,
        is_active=True,
    ))
    await db.commit()
    return RedirectResponse(f"/company/services/{service_id}/examples?saved=1", status_code=303)


@app.post("/company/services/{service_id}/examples/{image_id}/delete")
async def company_service_examples_delete(
    service_id: int,
    image_id: int,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from app.models.service_image import ServiceExampleImage
    company = await company_ctx(user, db)
    im = await db.get(ServiceExampleImage, image_id)
    if not im or im.company_id != company.id or im.service_id != service_id:
        raise HTTPException(404)
    await db.delete(im)
    await db.commit()
    return RedirectResponse(f"/company/services/{service_id}/examples", status_code=303)


@app.get("/company/service-images/{image_id}")
async def company_service_image_file(
    image_id: int,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    from fastapi.responses import Response
    from app.models.service_image import ServiceExampleImage
    from app.services.attachment_serve import media_root
    im = await db.get(ServiceExampleImage, image_id)
    if not im or im.company_id != user.company_id:
        raise HTTPException(404)
    p = media_root() / im.storage_path
    if not p.is_file():
        raise HTTPException(404, detail="Image file missing")
    return Response(
        content=p.read_bytes(),
        media_type=im.mime_type or "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/company/services/guide", response_class=HTMLResponse)
async def company_services_guide(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Static pricing setup guide — must be registered before /services/{service_id} routes."""
    company = await company_ctx(user, db)
    from app.data.service_setup_guides import all_guides
    return render(request, "company/service_guide.html", {
        "active": "services",
        "company_name": getattr(company, "name", "") if company else "",
        "user_name": getattr(user, "full_name", "") or "",
        "guides": all_guides(),
    })


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
            "min_qty": int(getattr(s, "min_qty", None) or 1),
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
    min_qty: int = Form(1),
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
    try:
        s.min_qty = max(1, int(min_qty or 1))
    except Exception:
        s.min_qty = 1
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
    """List orders with raw SQL only — avoids MissingGreenlet from ORM enums/lazy loads."""
    from sqlalchemy import text as sa_text

    cid = int(getattr(user, "company_id", 0) or 0)
    company_name = "Company"
    currency = "NGN"
    user_name = getattr(user, "full_name", None) or "Admin"
    rows = []

    try:
        crow = (await db.execute(sa_text(
            "SELECT id, name, currency FROM companies WHERE id = :id"
        ), {"id": cid})).mappings().first()
        if crow:
            company_name = crow.get("name") or company_name
            currency = crow.get("currency") or currency
    except Exception as e:
        print("orders company load:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass

    try:
        result = await db.execute(sa_text("""
            SELECT id,
                   COALESCE(customer_name, customer_wa_id, '—') AS customer,
                   COALESCE(service_name, '—') AS service,
                   COALESCE(status::text, 'pending') AS status,
                   COALESCE(total, 0) AS total,
                   COALESCE(currency, :cur) AS currency
            FROM orders
            WHERE company_id = :cid
            ORDER BY id DESC
            LIMIT 200
        """), {"cid": cid, "cur": currency})
        for o in result.mappings().all():
            st = str(o.get("status") or "pending").lower().replace("orderstatus.", "")
            rows.append({
                "id": o["id"],
                "customer": o.get("customer") or "—",
                "service": o.get("service") or "—",
                "status": st.replace("_", " "),
                "status_class": "pending" if any(x in st for x in ("await", "payment", "submitted")) else "active",
                "total": f"{o.get('currency') or currency} {float(o.get('total') or 0):,.0f}",
            })
    except Exception as e:
        print("orders query:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # Minimal fallback without status cast
        try:
            result = await db.execute(sa_text("""
                SELECT id, customer_wa_id, service_name, total, currency
                FROM orders WHERE company_id = :cid ORDER BY id DESC LIMIT 200
            """), {"cid": cid})
            for o in result.mappings().all():
                rows.append({
                    "id": o["id"],
                    "customer": o.get("customer_wa_id") or "—",
                    "service": o.get("service_name") or "—",
                    "status": "pending",
                    "status_class": "pending",
                    "total": f"{o.get('currency') or currency} {float(o.get('total') or 0):,.0f}",
                })
        except Exception as e2:
            print("orders query fallback:", type(e2).__name__, e2)
            try:
                await db.rollback()
            except Exception:
                pass

    return render(request, "company/orders.html", {
        "active": "orders",
        "company_name": company_name,
        "user_name": user_name,
        "orders": rows,
    })


@app.get("/company/orders/{order_id}", response_class=HTMLResponse)
async def company_order_detail(
    order_id: int, request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Order detail via raw SQL — no ORM enum greenlet issues."""
    from sqlalchemy import text as sa_text

    cid = int(getattr(user, "company_id", 0) or 0)
    company_name = "Company"
    user_name = getattr(user, "full_name", None) or "Admin"
    currency = "NGN"

    try:
        crow = (await db.execute(sa_text(
            "SELECT name, currency FROM companies WHERE id = :id"
        ), {"id": cid})).mappings().first()
        if crow:
            company_name = crow.get("name") or company_name
            currency = crow.get("currency") or currency
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass

    order_row = None
    try:
        order_row = (await db.execute(sa_text("""
            SELECT id, customer_wa_id, customer_name, service_name, details,
                   COALESCE(total, 0) AS total, COALESCE(currency, :cur) AS currency,
                   COALESCE(status::text, 'pending') AS status,
                   fulfillment, payment_proof, conversation_id
            FROM orders
            WHERE id = :oid AND company_id = :cid
        """), {"oid": order_id, "cid": cid, "cur": currency})).mappings().first()
    except Exception as e:
        print("order detail:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        try:
            order_row = (await db.execute(sa_text("""
                SELECT id, customer_wa_id, service_name, total, currency, payment_proof, conversation_id
                FROM orders WHERE id = :oid AND company_id = :cid
            """), {"oid": order_id, "cid": cid})).mappings().first()
        except Exception as e2:
            print("order detail fallback:", e2)
            try:
                await db.rollback()
            except Exception:
                pass

    if not order_row:
        raise HTTPException(404, "Order not found")

    st = str(order_row.get("status") or "pending").lower().replace("orderstatus.", "")
    proof = order_row.get("payment_proof") or ""
    return render(request, "company/order_detail.html", {
        "active": "orders",
        "company_name": company_name,
        "user_name": user_name,
        "order_id": order_row["id"],
        "customer": order_row.get("customer_name") or order_row.get("customer_wa_id") or "—",
        "service": order_row.get("service_name") or "—",
        "total": f"{order_row.get('currency') or currency} {float(order_row.get('total') or 0):,.0f}",
        "status": st.replace("_", " "),
        "payment_proof": proof,
        "details": order_row.get("details") or "",
        "fulfillment": order_row.get("fulfillment") or "",
        "conversation_id": order_row.get("conversation_id"),
    })


@app.post("/company/orders/{order_id}/status")
async def company_order_status(
    order_id: int, status: str = Form(...), note: Optional[str] = Form(None),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    cid = int(getattr(user, "company_id", 0) or 0)
    try:
        await db.execute(sa_text("""
            UPDATE orders
            SET status = :st,
                status_note = :note,
                updated_at = NOW()
            WHERE id = :oid AND company_id = :cid
        """), {"st": status, "note": note, "oid": order_id, "cid": cid})
        await db.commit()
    except Exception as e:
        print("order status update:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass
        # without updated_at
        try:
            await db.execute(sa_text(
                "UPDATE orders SET status = :st WHERE id = :oid AND company_id = :cid"
            ), {"st": status, "oid": order_id, "cid": cid})
            await db.commit()
        except Exception as e2:
            print("order status fallback:", e2)
            try:
                await db.rollback()
            except Exception:
                pass
    try:
        from app.services.bot_engine import notify_order_status
        # load minimal order-like object for notify
        class _O:
            pass
        o = _O()
        o.id = order_id
        o.company_id = cid
        o.status = status
        o.customer_wa_id = None
        o.conversation_id = None
        row = (await db.execute(sa_text(
            "SELECT customer_wa_id, conversation_id FROM orders WHERE id = :oid"
        ), {"oid": order_id})).mappings().first()
        if row:
            o.customer_wa_id = row.get("customer_wa_id")
            o.conversation_id = row.get("conversation_id")
        await notify_order_status(db, o, status, note)
    except Exception as e:
        print("notify_order_status:", e)
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




@app.get("/company/attachments/{attachment_id}")
async def company_attachment_file(
    attachment_id: int,
    request: Request,
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    """Serve customer/admin file. Re-downloads from WhatsApp if missing on disk (Render-safe)."""
    from fastapi.responses import Response
    from app.models.conversation import Attachment
    from app.services.attachment_serve import resolve_bytes

    att = await db.get(Attachment, attachment_id)
    if not att or att.company_id != user.company_id:
        raise HTTPException(404, detail="Attachment not found")
    resolved = await resolve_bytes(db, att)
    if not resolved:
        raise HTTPException(404, detail="File not available")
    content, mime, fname = resolved
    force_dl = request.query_params.get("download") in ("1", "true", "yes")
    disp = "attachment" if force_dl or not (mime or "").startswith("image/") else "inline"
    return Response(
        content=content,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disp}; filename="{fname}"',
            "Cache-Control": "private, max-age=3600",
        },
    )


@app.get("/company/messages", response_class=HTMLResponse)
async def company_messages(request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    from app.models.conversation import Conversation, Message, Attachment, Order
    from pathlib import Path as _P
    company = await company_ctx(user, db)
    convs = (await db.execute(
        select(Conversation).where(Conversation.company_id == company.id).order_by(Conversation.updated_at.desc())
    )).scalars().all()
    selected_id = request.query_params.get("c")
    messages_view = []
    selected = None
    if selected_id:
        try:
            cid = int(selected_id)
        except ValueError:
            cid = None
        if cid:
            selected = await db.get(Conversation, cid)
            if selected and selected.company_id == company.id:
                rows = list((await db.execute(
                    select(Message).where(Message.conversation_id == cid).order_by(Message.created_at.asc())
                )).scalars().all())
                for m in rows:
                    body = m.body or ""
                    media_src = None
                    media_kind = None
                    media_name = None
                    att = None
                    aid = getattr(m, "attachment_id", None)
                    if aid:
                        att = await db.get(Attachment, aid)
                    if att:
                        media_src = f"/company/attachments/{att.id}"
                        mime = (att.mime_type or "").lower()
                        if mime.startswith("image/"):
                            media_kind = "image"
                        elif mime.startswith("video/"):
                            media_kind = "video"
                        elif mime.startswith("audio/"):
                            media_kind = "audio"
                        else:
                            media_kind = "document"
                        media_name = att.original_name
                        if body in ("[media]", "[image]", "[document]", "[audio]", ""):
                            body = att.original_name or ("Image" if media_kind == "image" else "Attachment")
                    else:
                        mu = getattr(m, "media_url", None) or ""
                        if mu.startswith("/company/attachments/"):
                            media_src = mu
                            media_kind = "image"
                        elif mu.startswith("/media/") and not mu.rstrip("/").endswith(tuple("0123456789")):
                            # only if it looks like a real file path, not a bare WA id
                            media_src = mu
                            media_kind = "image"

                    order_summary = None
                    if m.direction == "outbound" and body and "payment" in body.lower():
                        try:
                            od = (await db.execute(
                                select(Order).where(
                                    Order.company_id == company.id,
                                    Order.customer_wa_id == selected.customer_wa_id,
                                ).order_by(Order.id.desc()).limit(1)
                            )).scalars().first()
                            if od:
                                order_summary = (
                                    f"{od.service_name or 'Order'} · "
                                    f"{getattr(od, 'details', None) or ''} · "
                                    f"{od.currency or company.currency} {float(od.total or od.total_amount or 0):,.0f}"
                                ).strip(" ·")
                        except Exception:
                            pass

                    messages_view.append({
                        "direction": m.direction,
                        "body": body,
                        "media_src": media_src,
                        "media_kind": media_kind,
                        "media_name": media_name,
                        "order_summary": order_summary,
                    })
    threads = []
    for c in convs:
        last = (await db.execute(
            select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at.desc()).limit(1)
        )).scalars().first()
        preview = "—"
        if last:
            b = last.body or ""
            if b in ("[media]", "[image]", "") and getattr(last, "media_url", None):
                preview = "📎 Attachment"
            else:
                preview = (b or "—")[:80]
        threads.append({
            "id": c.id,
            "wa": c.customer_wa_id,
            "name": c.customer_name or c.customer_wa_id,
            "needs_human": bool(getattr(c, "needs_human", False) or getattr(c, "is_live_takeover", False)),
            "preview": preview,
            "state": c.state,
        })
    return render(request, "company/messages.html", {
        "active": "messages",
        "company_name": company.name,
        "user_name": user.full_name,
        "threads": threads,
        "messages": messages_view,
        "selected": selected,
        "selected_id": int(selected_id) if selected_id and str(selected_id).isdigit() else None,
    })





@app.post("/company/messages/reply")
async def company_messages_reply(
    request: Request,
    conversation_id: int = Form(...),
    body: str = Form(""),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import Conversation, Message, Attachment
    from app.models.company import CompanyWhatsAppNumber
    from app.services.whatsapp_send import (
        send_text, upload_media_bytes, send_document, send_image_id, send_video_id,
    )
    from app.config import get_settings
    import uuid
    from pathlib import Path as _P

    company = await company_ctx(user, db)
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != company.id:
        raise HTTPException(404)

    form = await request.form()
    upload = form.get("file")
    text = (body or "").strip()

    conv.is_live_takeover = True
    conv.needs_human = True

    settings = get_settings()
    media_root = _P(getattr(settings, "MEDIA_ROOT", "uploads"))
    if not media_root.is_absolute():
        media_root = (Path(__file__).resolve().parent / media_root).resolve()
    media_root.mkdir(parents=True, exist_ok=True)

    media_url = None
    att_id = None
    file_bytes = None
    mime = "application/octet-stream"
    filename = "file.bin"

    if upload is not None and hasattr(upload, "filename") and upload.filename:
        file_bytes = await upload.read()
        if file_bytes:
            filename = upload.filename
            ext = _P(filename).suffix or ".bin"
            fname = f"admin_{company.id}_{uuid.uuid4().hex[:12]}{ext}"
            company_dir = media_root / str(company.id)
            company_dir.mkdir(parents=True, exist_ok=True)
            dest = company_dir / fname
            dest.write_bytes(file_bytes)
            media_url = f"/media/{company.id}/{fname}"
            mime = getattr(upload, "content_type", None) or "application/octet-stream"
            att = Attachment(
                company_id=company.id,
                customer_wa_id=conv.customer_wa_id,
                conversation_id=conv.id,
                kind="admin_upload",
                original_name=filename,
                mime_type=mime,
                storage_path=f"{company.id}/{fname}",
                size_bytes=len(file_bytes),
            )
            db.add(att)
            await db.flush()
            att_id = att.id

    display = text or (filename if file_bytes else "Attachment")
    db.add(Message(
        conversation_id=conv.id,
        direction="outbound",
        body=display,
        media_url=media_url,
        attachment_id=att_id,
    ))
    await db.commit()

    link = (await db.execute(
        select(CompanyWhatsAppNumber).where(
            CompanyWhatsAppNumber.company_id == company.id,
            CompanyWhatsAppNumber.is_active == True,  # noqa: E712
        )
    )).scalars().first()

    if link and link.access_token:
        try:
            if file_bytes:
                mid = await upload_media_bytes(
                    link.phone_number_id, link.access_token, file_bytes, mime, filename,
                )
                if mid:
                    if mime.startswith("image/"):
                        await send_image_id(
                            link.phone_number_id, link.access_token, conv.customer_wa_id, mid, caption=text or None,
                        )
                    elif mime.startswith("video/"):
                        await send_video_id(
                            link.phone_number_id, link.access_token, conv.customer_wa_id, mid, caption=text or None,
                        )
                    else:
                        await send_document(
                            link.phone_number_id, link.access_token, conv.customer_wa_id,
                            media_id=mid, filename=filename, caption=text or None,
                        )
                else:
                    # Fallback note if upload failed
                    await send_text(
                        link.phone_number_id, link.access_token, conv.customer_wa_id,
                        (text + "\n" if text else "") + f"📎 File: {filename} (delivery to WhatsApp failed — check token)",
                    )
            elif text:
                await send_text(link.phone_number_id, link.access_token, conv.customer_wa_id, text)
        except Exception as e:
            print("admin_reply_wa", type(e).__name__, e)

    return RedirectResponse(f"/company/messages?c={conversation_id}&sent=1", status_code=303)




@app.post("/company/messages/clear")
async def company_messages_clear(
    conversation_id: int = Form(...),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Clear chat in admin panel only — does not delete customer WhatsApp history."""
    from app.models.conversation import Conversation, Message
    from sqlalchemy import delete
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != user.company_id:
        raise HTTPException(404)
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await db.commit()
    return RedirectResponse(f"/company/messages?c={conversation_id}&cleared=1", status_code=303)


@app.post("/company/messages/pin")
async def company_messages_pin(
    conversation_id: int = Form(...),
    message_id: int = Form(...),
    days: int = Form(1),
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import Conversation, Message
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sa_text
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.company_id != user.company_id:
        raise HTTPException(404)
    msg = await db.get(Message, message_id)
    if not msg or msg.conversation_id != conversation_id:
        raise HTTPException(404)
    days = 1 if days not in (1, 7, 30) else days
    until = datetime.now(timezone.utc) + timedelta(days=days)
    # ensure column
    try:
        await db.execute(sa_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS pinned_until TIMESTAMPTZ"))
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
    try:
        await db.execute(sa_text(
            "UPDATE messages SET pinned_until = :u WHERE id = :id"
        ), {"u": until, "id": message_id})
        await db.commit()
    except Exception as e:
        print("pin", e)
        try:
            await db.rollback()
        except Exception:
            pass
    return RedirectResponse(f"/company/messages?c={conversation_id}", status_code=303)


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




@app.get("/company/api/notifications")
async def company_notifications_api_alias(user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    return await company_notifications_api(user=user, db=db)



@app.post("/company/api/notifications/read-all")
async def company_notifications_read_all(
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    cid = getattr(user, "company_id", None)
    if not cid:
        return {"ok": False}
    try:
        await db.execute(sa_text(
            "UPDATE admin_notifications SET is_read = true WHERE company_id = :cid AND COALESCE(is_read, false) = false"
        ), {"cid": int(cid)})
        await db.commit()
    except Exception as e:
        print("read_all", e)
        try:
            await db.rollback()
        except Exception:
            pass
    return {"ok": True}


@app.post("/company/api/notifications/clear")
async def company_notifications_clear(
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Remove all notifications for this company (does not affect WhatsApp)."""
    from sqlalchemy import text as sa_text
    cid = getattr(user, "company_id", None)
    if not cid:
        return {"ok": False}
    try:
        await db.execute(sa_text("DELETE FROM admin_notifications WHERE company_id = :cid"), {"cid": int(cid)})
        await db.commit()
    except Exception as e:
        print("clear_notif", e)
        try:
            await db.rollback()
        except Exception:
            pass
    return {"ok": True}



@app.get("/api/company/notifications")
async def company_notifications_api(
    user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    """Unread + recent platform notes for this company admin bell."""
    from sqlalchemy import text as sa_text

    cid = getattr(user, "company_id", None)
    if not cid:
        return []
    cid = int(cid)
    out = []
    seen = set()

    # 1) admin_notifications
    try:
        result = await db.execute(sa_text("""
            SELECT id, title, body, COALESCE(priority, 'high') AS priority,
                   conversation_id, created_at
            FROM admin_notifications
            WHERE company_id = :cid
              AND COALESCE(is_read, false) = false
            ORDER BY id DESC
            LIMIT 40
        """), {"cid": cid})
        for n in result.mappings().all():
            nid = int(n["id"])
            seen.add(("n", nid))
            title = n.get("title") or "Notification"
            body = n.get("body") or ""
            pri = str(n.get("priority") or "high").lower()
            if "platform" in title.lower():
                pri = "high"
            created = n.get("created_at")
            time_s = ""
            if created is not None:
                try:
                    time_s = created.strftime("%d %b %Y %H:%M")
                except Exception:
                    time_s = str(created)[:16]
            conv_id = n.get("conversation_id")
            out.append({
                "id": nid,
                "title": title,
                "body": body,
                "priority": pri,
                "kind": "platform" if "platform" in title.lower() else "order",
                "conversation_id": conv_id,
                "link": f"/company/messages?c={conv_id}" if conv_id else "/company/dashboard",
                "created_at": time_s,
                "time": time_s,
            })
    except Exception as e:
        print("notifications_api primary:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass

    # 2) Always merge recent platform_messages for this company (last 15)
    try:
        result = await db.execute(sa_text("""
            SELECT id, body, created_at, company_name
            FROM platform_messages
            WHERE company_id = :cid
            ORDER BY id DESC
            LIMIT 15
        """), {"cid": cid})
        for mrow in result.mappings().all():
            mid = int(mrow["id"])
            if ("pm", mid) in seen:
                continue
            # Avoid duplicates of same body already in out
            body = mrow.get("body") or ""
            if any((x.get("body") or "") == body for x in out):
                continue
            created = mrow.get("created_at")
            time_s = ""
            if created is not None:
                try:
                    time_s = created.strftime("%d %b %Y %H:%M")
                except Exception:
                    time_s = str(created)[:16]
            out.append({
                "id": 900000 + mid,
                "title": "Message from Client-RaQ Platform",
                "body": body,
                "priority": "high",
                "kind": "platform",
                "conversation_id": None,
                "link": "/company/dashboard",
                "created_at": time_s,
                "time": time_s,
            })
    except Exception as e:
        print("notifications_api platform_messages:", type(e).__name__, e)
        try:
            await db.rollback()
        except Exception:
            pass

    out.sort(key=lambda x: x.get("id") or 0, reverse=True)
    return out[:40]



@app.post("/company/api/notifications/{nid}/read")
async def company_notification_read_alias(nid: int, user: User = Depends(require_company), db: AsyncSession = Depends(get_db)):
    return await company_notification_read(nid=nid, user=user, db=db)


@app.post("/api/company/notifications/{nid}/read")
async def company_notification_read(
    nid: int, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    cid = getattr(user, "company_id", None)
    if not cid:
        return {"ok": False}
    try:
        await db.execute(sa_text("""
            UPDATE admin_notifications
            SET is_read = true
            WHERE id = :nid AND company_id = :cid
        """), {"nid": int(nid), "cid": int(cid)})
        await db.commit()
    except Exception as e:
        print("notif read:", e)
        try:
            await db.rollback()
        except Exception:
            pass
    return {"ok": True}



@app.get("/company/revenue/statement", response_class=HTMLResponse)
async def company_revenue_statement_page(
    request: Request, user: User = Depends(require_company), db: AsyncSession = Depends(get_db),
):
    company = await company_ctx(user, db)
    return render(request, "company/revenue_statement.html", {
        "active": "dashboard",
        "company_name": getattr(company, "name", "") if company else "",
        "user_name": getattr(user, "full_name", "") or "",
        "error": request.query_params.get("error"),
    })


@app.get("/company/revenue/pdf")
async def company_revenue_pdf_redirect():
    return RedirectResponse("/company/revenue/statement", status_code=303)


@app.post("/company/revenue/pdf")
async def company_revenue_pdf(
    password: str = Form(...),
    user: User = Depends(require_company),
    db: AsyncSession = Depends(get_db),
):
    """Bank-style Client RaQ statement. Requires company admin password."""
    from fastapi.responses import Response
    from datetime import datetime, timezone
    from io import BytesIO

    if not verify_password(password, user.hashed_password):
        return RedirectResponse("/company/revenue/statement?error=bad_password", status_code=303)

    company = await company_ctx(user, db)
    if not company:
        raise HTTPException(404, "Company not found")

    company_id = int(company.id)
    company_name = str(getattr(company, "name", None) or "Company")
    company_slug = str(getattr(company, "slug", None) or company_id)
    cur = str(getattr(company, "currency", None) or "NGN")
    country = str(getattr(company, "country", None) or "")

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
            ).order_by(Order.created_at.desc()).limit(300)
        )
        orders = list(result.scalars().all())
        for o in orders:
            st = getattr(o, "status", None)
            st = st.value if hasattr(st, "value") else str(st or "")
            created = getattr(o, "created_at", None)
            dt_s = created.strftime("%d %b %Y") if created is not None else "—"
            amt = getattr(o, "total", None)
            if amt is None:
                amt = getattr(o, "total_amount", 0) or 0
            cust = getattr(o, "customer_name", None) or getattr(o, "customer_wa_id", None) or "—"
            svc = getattr(o, "service_name", None) or "—"
            rows.append({
                "id": int(o.id),
                "date": dt_s,
                "customer": str(cust)[:28],
                "service": str(svc)[:26],
                "amount": float(amt or 0),
                "status": str(st).replace("_", " ").title(),
            })
    except Exception as e:
        print("revenue_pdf orders:", e)
        try:
            await db.rollback()
        except Exception:
            pass

    total_all = sum(r["amount"] for r in rows)
    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    stmt_ref = f"CRQ-{company_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        from reportlab.lib.colors import HexColor, white, black

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        width, height = A4
        try:
            c.setEncrypt(password)
        except Exception:
            pass

        navy = HexColor("#0B1F3A")
        accent = HexColor("#1B4F72")
        light = HexColor("#F4F6F8")
        muted = HexColor("#5D6D7E")
        line_c = HexColor("#D5D8DC")
        green = HexColor("#0E6655")

        def draw_header(page_no: int):
            c.setFillColor(navy)
            c.rect(0, height - 32 * mm, width, 32 * mm, fill=1, stroke=0)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(18 * mm, height - 14 * mm, "Client RaQ")
            c.setFont("Helvetica", 9)
            c.drawString(18 * mm, height - 20 * mm, "Revenue statement")
            c.setFont("Helvetica", 8)
            c.drawRightString(width - 18 * mm, height - 14 * mm, company_name)
            c.drawRightString(width - 18 * mm, height - 19 * mm, f"Ref: {stmt_ref}")
            c.setFillColor(accent)
            c.rect(0, height - 34 * mm, width, 2 * mm, fill=1, stroke=0)

        def draw_footer(page_no: int):
            c.setStrokeColor(line_c)
            c.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
            c.setFillColor(muted)
            c.setFont("Helvetica", 7)
            c.drawString(18 * mm, 9 * mm, "Generated by Client RaQ · Confidential · For account holder only")
            c.drawRightString(width - 18 * mm, 9 * mm, f"Page {page_no}")

        page = 1
        draw_header(page)
        y = height - 42 * mm

        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(18 * mm, y, "Account summary")
        y -= 6 * mm
        c.setFont("Helvetica", 9)
        c.setFillColor(muted)
        c.drawString(18 * mm, y, f"Business: {company_name}")
        y -= 4.5 * mm
        if country:
            c.drawString(18 * mm, y, f"Country: {country}")
            y -= 4.5 * mm
        c.drawString(18 * mm, y, f"Currency: {cur}")
        y -= 4.5 * mm
        c.drawString(18 * mm, y, f"Generated: {generated}")
        y -= 8 * mm

        box_w = (width - 36 * mm - 6 * mm) / 3
        labels = [("Today", rev.get("daily", 0)), ("This week", rev.get("weekly", 0)), ("This month", rev.get("monthly", 0))]
        x0 = 18 * mm
        for i, (lab, val) in enumerate(labels):
            x = x0 + i * (box_w + 3 * mm)
            c.setFillColor(light)
            c.roundRect(x, y - 16 * mm, box_w, 18 * mm, 3, fill=1, stroke=0)
            c.setFillColor(muted)
            c.setFont("Helvetica", 8)
            c.drawString(x + 3 * mm, y - 4 * mm, lab)
            c.setFillColor(navy)
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x + 3 * mm, y - 11 * mm, f"{cur} {float(val or 0):,.0f}")
        y -= 26 * mm

        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(18 * mm, y, "Confirmed payment activity")
        y -= 6 * mm

        c.setFillColor(navy)
        c.rect(18 * mm, y - 5 * mm, width - 36 * mm, 7 * mm, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(20 * mm, y - 3.2 * mm, "Date")
        c.drawString(42 * mm, y - 3.2 * mm, "Ref")
        c.drawString(58 * mm, y - 3.2 * mm, "Customer")
        c.drawString(100 * mm, y - 3.2 * mm, "Service")
        c.drawRightString(width - 42 * mm, y - 3.2 * mm, "Amount")
        c.drawString(width - 38 * mm, y - 3.2 * mm, "Status")
        y -= 9 * mm

        c.setFont("Helvetica", 8)
        if not rows:
            c.setFillColor(muted)
            c.drawString(20 * mm, y, "No confirmed payment orders in this period.")
            y -= 6 * mm
        else:
            for r in rows:
                if y < 28 * mm:
                    draw_footer(page)
                    c.showPage()
                    page += 1
                    draw_header(page)
                    y = height - 42 * mm
                    c.setFillColor(navy)
                    c.rect(18 * mm, y - 5 * mm, width - 36 * mm, 7 * mm, fill=1, stroke=0)
                    c.setFillColor(white)
                    c.setFont("Helvetica-Bold", 8)
                    c.drawString(20 * mm, y - 3.2 * mm, "Date")
                    c.drawString(42 * mm, y - 3.2 * mm, "Ref")
                    c.drawString(58 * mm, y - 3.2 * mm, "Customer")
                    c.drawString(100 * mm, y - 3.2 * mm, "Service")
                    c.drawRightString(width - 42 * mm, y - 3.2 * mm, "Amount")
                    c.drawString(width - 38 * mm, y - 3.2 * mm, "Status")
                    y -= 9 * mm
                    c.setFont("Helvetica", 8)
                c.setFillColor(black)
                c.drawString(20 * mm, y, r["date"][:12])
                c.drawString(42 * mm, y, f"#{r['id']}")
                c.drawString(58 * mm, y, r["customer"][:22])
                c.drawString(100 * mm, y, r["service"][:20])
                c.setFillColor(green)
                c.drawRightString(width - 42 * mm, y, f"{r['amount']:,.0f}")
                c.setFillColor(muted)
                c.drawString(width - 38 * mm, y, r["status"][:12])
                c.setStrokeColor(line_c)
                c.line(18 * mm, y - 1.8 * mm, width - 18 * mm, y - 1.8 * mm)
                y -= 6 * mm

        y -= 4 * mm
        if y < 40 * mm:
            draw_footer(page)
            c.showPage()
            page += 1
            draw_header(page)
            y = height - 42 * mm
        c.setFillColor(light)
        c.roundRect(18 * mm, y - 14 * mm, width - 36 * mm, 16 * mm, 3, fill=1, stroke=0)
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(22 * mm, y - 6 * mm, "Total (listed confirmed orders)")
        c.drawRightString(width - 22 * mm, y - 6 * mm, f"{cur} {total_all:,.0f}")
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        c.drawString(22 * mm, y - 11 * mm, "Includes payment confirmed and later production statuses only.")

        y -= 24 * mm
        c.setFillColor(muted)
        c.setFont("Helvetica", 7)
        for line in (
            "This statement is generated from your Client RaQ order records.",
            "It is not a bank document. Use it for internal bookkeeping and client reconciliation.",
            "PDF is password-protected with your company admin login password.",
            "Client RaQ · clientraq.com",
        ):
            if y < 20 * mm:
                break
            c.drawString(18 * mm, y, line)
            y -= 3.5 * mm

        draw_footer(page)
        c.save()
        pdf_bytes = buf.getvalue()
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in company_slug)[:40]
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="ClientRaQ_Statement_{safe_name}.pdf"'},
        )
    except Exception as e:
        print("revenue_pdf reportlab:", e)
        lines = [
            "CLIENT RAQ REVENUE STATEMENT",
            f"Company: {company_name}",
            f"Ref: {stmt_ref}",
            f"Generated: {generated}",
            f"Today: {cur} {float(rev.get('daily', 0) or 0):,.0f}",
            f"Week: {cur} {float(rev.get('weekly', 0) or 0):,.0f}",
            f"Month: {cur} {float(rev.get('monthly', 0) or 0):,.0f}",
            "",
            "Orders:",
        ]
        for r in rows:
            lines.append(f"{r['date']} #{r['id']} {r['customer']} {r['service']} {r['amount']:,.0f} {r['status']}")
        lines.append(f"Total: {cur} {total_all:,.0f}")
        return Response("\n".join(lines), media_type="text/plain; charset=utf-8")



@app.get("/health")
async def health():
    return {"status": "ok", "app": "Client-RaQ"}
