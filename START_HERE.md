# Client-RaQ — clean start

## 1. Reset database (strongly recommended)

In pgAdmin Query Tool:

```sql
DROP DATABASE IF EXISTS client_raq;
CREATE DATABASE client_raq;
```

## 2. .env

```
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/client_raq
PLATFORM_ADMIN_EMAIL=gallantcreativity@gmail.com
PLATFORM_ADMIN_PASSWORD=RaQAdmin2026
WHATSAPP_VERIFY_TOKEN=client_raq_verify_2026
GROK_API_KEY=
SECRET_KEY=ClientRaQSuperSecretKey2026ForPrintingBotPlatform
```

Password with @ must use %40.

## 3. Run

```powershell
cd client_raq_frontend
py -3.12 -m venv venv
.\venv\Scripts\activate
py -m pip install -r requirements.txt
py -m scripts.seed
py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 4. Login URLs

- Platform: http://localhost:8000/platform/login
- Company (after you create one): http://localhost:8000/company/login
  - Brand = slug you set
  - Email + temp password shown on success page

Suspend / Activate: Platform → Companies.
