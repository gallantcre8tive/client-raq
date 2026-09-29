# FIX_V2_20260927
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
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
    """Idempotent repairs for drifted local DBs."""
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

        "ALTER TABLE services ADD COLUMN IF NOT EXISTS flow_type VARCHAR(40) DEFAULT 'generic'",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS design_fee DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS min_qty INTEGER DEFAULT 1",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS design_fee_default DOUBLE PRECISION DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS service_variants (
        id SERIAL PRIMARY KEY,
        service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
        label VARCHAR(80) NOT NULL,
        subtitle VARCHAR(120),
        price DOUBLE PRECISION DEFAULT 0,
        sort_order INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT true
    )""",

        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS country VARCHAR(100)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'NGN'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS phone VARCHAR(30)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS greeting_message TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS bot_language VARCHAR(20) DEFAULT 'both'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS bot_flags TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS delivery_fee_base DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS platform_note TEXT",
        "ALTER TABLE service_categories ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_json TEXT",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_live_takeover BOOLEAN DEFAULT false",
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
        "ALTER TABLE company_whatsapp_numbers ADD COLUMN IF NOT EXISTS waba_id VARCHAR(50)",
        "ALTER TABLE company_whatsapp_numbers ADD COLUMN IF NOT EXISTS access_token TEXT",
        "ALTER TABLE company_whatsapp_numbers ADD COLUMN IF NOT EXISTS display_number VARCHAR(30)",
        "ALTER TABLE company_whatsapp_numbers ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS pricing_method VARCHAR(30) DEFAULT 'piece'",
        "ALTER TABLE services ADD COLUMN IF NOT EXISTS catalog_key VARCHAR(120)",

    ]
    try:
        async with engine.begin() as conn:
            for sql in stmts:
                try:
                    await conn.execute(text(sql))
                except Exception:
                    pass
            # status as plain varchar
            for sql in [
                "ALTER TABLE companies ALTER COLUMN status DROP DEFAULT",
                "ALTER TABLE companies ALTER COLUMN status TYPE VARCHAR(20) USING status::text",
                "ALTER TABLE companies ALTER COLUMN status SET DEFAULT 'active'",
            ]:
                try:
                    await conn.execute(text(sql))
                except Exception:
                    pass
            # drop NOT NULL on optional company columns
            try:
                rows = (await conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='companies' AND is_nullable='NO'
                      AND column_name NOT IN ('id','name','slug')
                      AND column_default IS NULL
                """))).fetchall()
                for (col,) in rows:
                    try:
                        await conn.execute(text(f'ALTER TABLE companies ALTER COLUMN "{col}" DROP NOT NULL'))
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception as e:
        print("ensure_schema warning:", e)

async def init_db():
    from app import models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE services ADD COLUMN IF NOT EXISTS reference_image_url VARCHAR(500)"))
            await conn.commit()
        except Exception as _r:
            print("ref_img_col", _r)
            try:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS attachments (
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
                    )
                """))
                await conn.commit()
            except Exception as _ae:
                print("attachments_schema", _ae)
    await ensure_schema()
