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
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS media_url VARCHAR(500)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_proof VARCHAR(500)",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS schedule_note VARCHAR(200)",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS handoff_reason VARCHAR(300)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS subscription_ends_at TIMESTAMPTZ",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(40)",
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



async def ensure_billing_schema():
    """Billing tables — safe to run repeatedly."""
    from sqlalchemy import text
    stmts = [
        """CREATE TABLE IF NOT EXISTS platform_pricing (
            id SERIAL PRIMARY KEY,
            monthly_ngn DOUBLE PRECISION DEFAULT 18000,
            six_month_ngn DOUBLE PRECISION DEFAULT 102000,
            yearly_ngn DOUBLE PRECISION DEFAULT 198000,
            channel_addon_monthly_ngn DOUBLE PRECISION DEFAULT 18000,
            guide_fee_ngn DOUBLE PRECISION DEFAULT 5000,
            trial_hours INTEGER DEFAULT 48,
            trial_enabled BOOLEAN DEFAULT true,
            trial_message_limit INTEGER DEFAULT 50,
            six_month_includes_both_channels BOOLEAN DEFAULT true,
            yearly_includes_both_channels BOOLEAN DEFAULT true,
            support_whatsapp VARCHAR(40),
            support_email VARCHAR(255),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            status VARCHAR(30) DEFAULT 'trial',
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            trial_ends_at TIMESTAMPTZ,
            subscription_ends_at TIMESTAMPTZ,
            message_count_trial INTEGER DEFAULT 0,
            guide_unlocked BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS platform_payments (
            id SERIAL PRIMARY KEY,
            company_id INTEGER,
            email VARCHAR(255),
            reference VARCHAR(100) UNIQUE,
            amount_kobo INTEGER DEFAULT 0,
            currency VARCHAR(10) DEFAULT 'NGN',
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            include_guide BOOLEAN DEFAULT false,
            status VARCHAR(30) DEFAULT 'pending',
            paystack_raw TEXT,
            registration_token VARCHAR(64),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            paid_at TIMESTAMPTZ
        )""",
        """CREATE TABLE IF NOT EXISTS registration_tokens (
            id SERIAL PRIMARY KEY,
            token VARCHAR(64) UNIQUE,
            email VARCHAR(255),
            plan_code VARCHAR(40),
            channel_whatsapp BOOLEAN DEFAULT true,
            channel_telegram BOOLEAN DEFAULT false,
            include_guide BOOLEAN DEFAULT false,
            payment_reference VARCHAR(100),
            amount_kobo INTEGER DEFAULT 0,
            used BOOLEAN DEFAULT false,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS business_type VARCHAR(40) DEFAULT 'printing'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS website_url VARCHAR(300)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS telegram_bot_token VARCHAR(200)",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS telegram_enabled BOOLEAN DEFAULT false",

        """CREATE TABLE IF NOT EXISTS email_verifications (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255),
            code VARCHAR(12),
            purpose VARCHAR(40) DEFAULT 'signup',
            payload_json VARCHAR(4000),
            attempts INTEGER DEFAULT 0,
            used BOOLEAN DEFAULT false,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_instagram VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_x VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_facebook VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_tiktok VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_linkedin VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_youtube VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_whatsapp VARCHAR(200)",
        "ALTER TABLE platform_pricing ADD COLUMN IF NOT EXISTS social_telegram VARCHAR(200)",
        """CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            actor_user_id INTEGER,
            actor_email VARCHAR(255),
            company_id INTEGER,
            action VARCHAR(80),
            detail TEXT,
            ip VARCHAR(60),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",

    ]
    async with engine.begin() as conn:
        for sql in stmts:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                print("billing schema:", e)



async def init_db():
    """Create tables quickly. Never block Render port detection forever."""
    import asyncio
    from app import models  # noqa: F401

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # service example images
            try:
                await conn.execute(__import__('sqlalchemy').text('''CREATE TABLE IF NOT EXISTS service_example_images (
                    id SERIAL PRIMARY KEY,
                    company_id INTEGER,
                    service_id INTEGER,
                    storage_path VARCHAR(500),
                    original_name VARCHAR(255),
                    mime_type VARCHAR(100),
                    title VARCHAR(200),
                    description TEXT,
                    tags VARCHAR(500),
                    is_active BOOLEAN DEFAULT true,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )'''))
            except Exception:
                pass

    try:
        await asyncio.wait_for(_create(), timeout=25)
        print("init_db: create_all ok")
    except Exception as e:
        print("init_db create_all:", type(e).__name__, e)

    try:
        await asyncio.wait_for(ensure_schema(), timeout=45)
        try:
            await asyncio.wait_for(ensure_billing_schema(), timeout=20)
            print("init_db: billing schema ok")
        except Exception as be:
            print("init_db billing:", be)
        print("init_db: ensure_schema ok")
    except Exception as e:
        print("init_db ensure_schema:", type(e).__name__, e)
