# Client-RaQ – Full stack (Frontend UI + Backend)

## Stack
- FastAPI + Jinja2 (Obsidian Precision UI)
- SQLAlchemy 2.0 async + PostgreSQL
- Session cookie auth (platform + company)
- Multi-tenant isolation by `company_id`

## Setup
```bash
py -3.12 -m venv venv
# Windows: .\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit DATABASE_URL (encode @ in password as %40), PLATFORM_ADMIN_*

# Create DB in PostgreSQL: client_raq
py -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"
py -m scripts.seed

py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## URLs
- Landing: http://localhost:8000
- Platform login: /platform/login  (email/password from .env seed)
- Company login: /company/login  (brand + email + password after Add Company)

## Backend features live now
- Platform: create company + admin, list, suspend, delete, messages, settings
- Company: login scoped to brand, dashboard counts, services CRUD, payments, orders, bot settings, profile
- WhatsApp webhook verify + receive stub (bot engine next)

## Next
- Full WhatsApp conversation engine + Grok LLM
- Payment proof media storage
- Live chat takeover send
- Encrypted secrets store for platform settings
