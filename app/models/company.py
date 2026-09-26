import enum
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, Float, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class CompanyStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="NGN")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    greeting_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_language: Mapped[str] = mapped_column(String(20), default="both")
    bot_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_fee_base: Mapped[float] = mapped_column(Float, default=0)
    platform_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    users = relationship("User", back_populates="company", cascade="all, delete-orphan")
    payment_details = relationship("PaymentDetail", back_populates="company", cascade="all, delete-orphan")
    whatsapp_numbers = relationship("CompanyWhatsAppNumber", back_populates="company", cascade="all, delete-orphan")
    services = relationship("Service", back_populates="company", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="company", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="company", cascade="all, delete-orphan")

class PaymentDetail(Base):
    __tablename__ = "payment_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    bank_name: Mapped[str] = mapped_column(String(100))
    account_name: Mapped[str] = mapped_column(String(150))
    account_number: Mapped[str] = mapped_column(String(30))
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    company = relationship("Company", back_populates="payment_details")

class CompanyWhatsAppNumber(Base):
    __tablename__ = "company_whatsapp_numbers"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    phone_number_id: Mapped[str] = mapped_column(String(50), index=True)
    waba_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    company = relationship("Company", back_populates="whatsapp_numbers")
