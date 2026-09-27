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
    GROK_MODEL: str = "grok-2-latest"
    XAI_MODEL: str = ""
    SESSION_COOKIE: str = "crq_session"

    class Config:
        env_file = ".env"
        extra = "ignore"

@lru_cache
def get_settings() -> Settings:
    return Settings()
