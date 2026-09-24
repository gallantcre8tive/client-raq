"""Add any missing columns so old DBs match current models."""
import asyncio
from sqlalchemy import text
from app.database import engine

ALTERS = [
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS country VARCHAR(100)",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS phone VARCHAR(30)",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS greeting_message TEXT",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS bot_language VARCHAR(20) DEFAULT 'both'",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS platform_note TEXT",
    "ALTER TABLE companies ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'NGN'",
]

async def main():
    async with engine.begin() as conn:
        for sql in ALTERS:
            try:
                await conn.execute(text(sql))
                print("OK:", sql[:60])
            except Exception as e:
                print("Skip/err:", e)
    print("Schema migration done.")

if __name__ == "__main__":
    asyncio.run(main())
