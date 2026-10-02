from functools import lru_cache
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Client-RaQ"
    SECRET_KEY: str = "change-me-in-production-client-raq-2026"
    DEBUG: bool = True
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/client_raq"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    PLATFORM_ADMIN_EMAIL: str = "admin@clientraq.com"
    PLATFORM_ADMIN_PASSWORD: str = "ChangeMe123!"
    SUPPORT_WHATSAPP: str = "+2349169158961"
    WHATSAPP_VERIFY_TOKEN: str = "client_raq_verify"
    GROK_API_KEY: str = ""
    XAI_API_KEY: str = ""  # alias — either GROK_API_KEY or XAI_API_KEY works
    GROK_BASE_URL: str = "https://api.x.ai/v1"
    GROK_MODEL: str = "grok-4-fast-non-reasoning"
    XAI_MODEL: str = ""
    SESSION_COOKIE: str = "crq_session"
    SESSION_COOKIE_PLATFORM: str = "crq_platform_session"
    SESSION_COOKIE_COMPANY: str = "crq_company_session"
    # Optional: voice transcription (Whisper via Groq). Text bot works without it.
    GROQ_API_KEY: str = ""
    # Local folder for WhatsApp media (payment screenshots, designs)
    MEDIA_ROOT: str = "uploads"
    # Paystack (platform subscription payments)
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    APP_BASE_URL: str = "https://clientraq.com"
    # Email (signup verification) — leave empty to log codes in Render logs
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "Client-RaQ <noreply@clientraq.com>"


    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache
def get_settings() -> Settings:
    return Settings()
