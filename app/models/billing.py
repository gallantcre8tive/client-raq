"""Billing, subscriptions, trials, Paystack payments — multi-tenant SaaS layer."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, Float, Integer, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PlatformPricing(Base):
    """Single-row (id=1) admin-editable public prices in NGN."""
    __tablename__ = "platform_pricing"
    id: Mapped[int] = mapped_column(primary_key=True)
    monthly_ngn: Mapped[float] = mapped_column(Float, default=18000)
    six_month_ngn: Mapped[float] = mapped_column(Float, default=102000)
    yearly_ngn: Mapped[float] = mapped_column(Float, default=198000)
    channel_addon_monthly_ngn: Mapped[float] = mapped_column(Float, default=18000)
    guide_fee_ngn: Mapped[float] = mapped_column(Float, default=5000)
    trial_hours: Mapped[int] = mapped_column(Integer, default=48)
    trial_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_message_limit: Mapped[int] = mapped_column(Integer, default=50)
    six_month_includes_both_channels: Mapped[bool] = mapped_column(Boolean, default=True)
    yearly_includes_both_channels: Mapped[bool] = mapped_column(Boolean, default=True)
    support_whatsapp: Mapped[str | None] = mapped_column(String(40), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    social_instagram: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_x: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_facebook: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_tiktok: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_linkedin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_youtube: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_whatsapp: Mapped[str | None] = mapped_column(String(200), nullable=True)
    social_telegram: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Subscription(Base):
    """Per-company trial + paid access."""
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="trial")  # trial|active|expired|suspended
    plan_code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # monthly|six_month|yearly
    channel_whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count_trial: Mapped[int] = mapped_column(Integer, default=0)
    guide_unlocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Payment(Base):
    """Paystack payments for platform subscription (not end-customer print payments)."""
    __tablename__ = "platform_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    amount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(10), default="NGN")
    plan_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    channel_whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    include_guide: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|success|failed
    paystack_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RegistrationToken(Base):
    """One-time token after successful Paystack payment to create company account."""
    __tablename__ = "registration_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255))
    plan_code: Mapped[str] = mapped_column(String(40))
    channel_whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    include_guide: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
