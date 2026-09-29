import enum
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, TypeDecorator, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class UserRole(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"
    COMPANY_ADMIN = "company_admin"
    COMPANY_STAFF = "company_staff"


_ROLE_ALIASES = {
    "platform_admin": UserRole.PLATFORM_ADMIN,
    "PLATFORM_ADMIN": UserRole.PLATFORM_ADMIN,
    "platformadmin": UserRole.PLATFORM_ADMIN,
    "company_admin": UserRole.COMPANY_ADMIN,
    "COMPANY_ADMIN": UserRole.COMPANY_ADMIN,
    "companyadmin": UserRole.COMPANY_ADMIN,
    "company_staff": UserRole.COMPANY_STAFF,
    "COMPANY_STAFF": UserRole.COMPANY_STAFF,
    "companystaff": UserRole.COMPANY_STAFF,
}


class UserRoleColumn(TypeDecorator):
    """Accepts DB values PLATFORM_ADMIN or platform_admin without LookupError."""
    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, UserRole):
            return value.value
        key = str(value).strip()
        role = _ROLE_ALIASES.get(key) or _ROLE_ALIASES.get(key.lower().replace(" ", "_"))
        if role:
            return role.value
        return key.lower()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        key = str(value).strip()
        role = _ROLE_ALIASES.get(key) or _ROLE_ALIASES.get(key.lower().replace(" ", "_"))
        if role:
            return role
        # last resort: try enum by value
        try:
            return UserRole(key.lower())
        except Exception:
            return UserRole.COMPANY_ADMIN


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(UserRoleColumn(), default=UserRole.COMPANY_ADMIN)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    company = relationship("Company", back_populates="users")
