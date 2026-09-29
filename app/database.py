# Client-RaQ database layer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"timeout": 15},
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def ensure_schema():
    """Idempotent repairs. Each statement is isolated so one failure never hangs forever."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS broadcasts (
        id SERIAL PRIMARY KEY, company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        message TEXT, recipient_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0, created_by VARCHAR(150), created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
        """CREATE TABLE IF NOT EXISTS admin_notifications (
        id SERIAL PRIMARY KEY, company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        conversation_id INTEGER, title VARCHAR(200), body TEXT, priority VARCHAR(20) DEFAULT 'normal',
        is_read BOOLEAN DEFAULT false, created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
        """CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY, company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        wa_id VARCHAR(50), profile_name VARCHAR(150), language_preference VARCHAR(20),
        total_conversations INTEGER DEFAULT 0, last_order_id INTEGER,
        first_seen TIMESTAMPTZ DEFAULT NOW(), last_seen TIMESTAMPTZ DEFAULT NOW(), created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
        """CREATE TABLE IF NOT EXISTS platform_messages (
        id SERIAL PRIMARY KEY,
        company_id INTEGER,
        company_name VARCHAR(200) DEFAULT '',
        body TEXT NOT NULL,
        sent_by VARCHAR(200),
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
        """CREATE TABLE IF NOT EXISTS attachments (
        id SERIAL PRIMARY KEY,
        company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        customer_wa_id VARCHAR(50),
        conversation_id INTEGER,
        order_id INTEGER,
        message_id INTEGER,
        kind VARCHAR(40) DEFAULT 'file',
        original_name VARCHAR(255),
        mime_type VARCHAR(100),
        storage_path VARCHAR(500) NOT NULL,
        wa_media_id VARCHAR(120),
        size_bytes INTEGER,
        transcript TEXT,
        notes TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )""",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS needs_human BOOLEAN DEFAULT false",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS handoff_reason VARCHAR(300)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS custom_ai_instructions TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS bot_personality VARCHAR(40) DEFAULT 'friendly'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS business_hours TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS about_text TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS enquiry_whatsapp VARCHAR(40)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS enquiry_phone VARCHAR(40)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS enquiry_note VARCHAR(300)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS location_text VARCHAR(300)",
        "ALTER TABLE payment_details ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_type VARCHAR(40)",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachment_id INTEGER",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS reference_image_url VARCHAR(500)",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS flow_type VARCHAR(40) DEFAULT 'generic'",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS design_fee DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS min_qty INTEGER DEFAULT 1",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total_amount DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'NGN'",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS service_name VARCHAR(200)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS details TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment VARCHAR(20)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_date VARCHAR(40)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS scheduled_time VARCHAR(40)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_address TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS subtotal DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_proof_url VARCHAR(500)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status_note TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_wa_id VARCHAR(50)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name VARCHAR(150)",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS priority VARCHAR(20) DEFAULT 'normal'",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT false",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS body TEXT",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS conversation_id INTEGER",
        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS company_id INTEGER",

        "ALTER TABLE admin_notifications ADD COLUMN IF NOT EXISTS link_path VARCHAR(300)",
        "UPDATE users SET role = 'platform_admin' WHERE role::text IN ('PLATFORM_ADMIN','Platform_Admin','platformadmin')",
        "UPDATE users SET role = 'company_admin' WHERE role::text IN ('COMPANY_ADMIN','Company_Admin','companyadmin')",
        "UPDATE users SET role = 'company_staff' WHERE role::text IN ('COMPANY_STAFF','Company_Staff','companystaff')",
        "UPDATE orders SET total = total_amount WHERE (total IS NULL OR total = 0) AND COALESCE(total_amount,0) <> 0",
        "UPDATE orders SET total_amount = total WHERE (total_amount IS NULL OR total_amount = 0) AND COALESCE(total,0) <> 0",
    ]
    for stmt in stmts:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as e:
            print("ensure_schema skip:", str(e)[:120])


async def init_db():
    """Create tables quickly. Never block Render port detection forever."""
    import asyncio
    from app import models  # noqa: F401

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    try:
        await asyncio.wait_for(_create(), timeout=25)
        print("init_db: create_all ok")
    except Exception as e:
        print("init_db create_all:", type(e).__name__, e)

    try:
        await asyncio.wait_for(ensure_schema(), timeout=45)
        print("init_db: ensure_schema ok")
    except Exception as e:
        print("init_db ensure_schema:", type(e).__name__, e)
