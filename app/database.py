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
    await ensure_schema()
