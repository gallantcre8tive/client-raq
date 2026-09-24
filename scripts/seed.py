"""Create/update platform admin from .env. Safe category seed."""
import asyncio
from sqlalchemy import select, text
from app.database import AsyncSessionLocal, init_db
from app.models.user import User, UserRole
from app.core.security import hash_password
from app.config import get_settings

get_settings.cache_clear()
settings = get_settings()

CATEGORIES = [
    (1, "Large Format", "large-format"),
    (2, "Signage", "signage"),
    (3, "Cloth Branding", "cloth-branding"),
    (4, "Frames & Display", "frames"),
    (5, "Stickers & Labels", "stickers"),
    (6, "Stationery", "stationery"),
    (7, "Promotional Items", "promotional"),
    (8, "UV & Specialty", "uv-specialty"),
    (9, "Surprise Box", "surprise-box"),
]

async def seed():
    await init_db()
    email = (settings.PLATFORM_ADMIN_EMAIL or "").lower().strip()
    password = settings.PLATFORM_ADMIN_PASSWORD or ""
    if len(password) >= 2 and password[0] == password[-1] and password[0] in "\"'":
        password = password[1:-1]
    if not email or not password:
        print("ERROR: set PLATFORM_ADMIN_EMAIL and PLATFORM_ADMIN_PASSWORD in .env")
        return
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.email == email))
        user = r.scalar_one_or_none()
        if not user:
            db.add(User(
                email=email,
                hashed_password=hash_password(password),
                full_name="Platform Administrator",
                role=UserRole.PLATFORM_ADMIN,
                is_active=True,
            ))
            print("Created platform admin:", email)
        else:
            user.hashed_password = hash_password(password)
            user.role = UserRole.PLATFORM_ADMIN
            user.is_active = True
            print("Updated platform admin password:", email)
        await db.commit()
        for order, name, slug in CATEGORIES:
            try:
                await db.execute(text(
                    "INSERT INTO service_categories (name, slug, is_active, sort_order) "
                    "VALUES (:n, :s, true, :o) ON CONFLICT (slug) DO NOTHING"
                ), {"n": name, "s": slug, "o": order})
            except Exception:
                try:
                    await db.execute(text(
                        "INSERT INTO service_categories (name, slug, is_active) "
                        "SELECT :n, :s, true WHERE NOT EXISTS "
                        "(SELECT 1 FROM service_categories WHERE slug = :s)"
                    ), {"n": name, "s": slug})
                except Exception as e:
                    print("category skip", slug, e)
        await db.commit()
        print("Seed OK")
        print("Login email:", email)

if __name__ == "__main__":
    asyncio.run(seed())
