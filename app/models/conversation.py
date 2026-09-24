import enum
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey, Float, Boolean, Enum as SAEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class OrderStatus(str, enum.Enum):
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    PAYMENT_SUBMITTED = "payment_submitted"
    PAYMENT_CONFIRMED = "payment_confirmed"
    IN_PRODUCTION = "in_production"
    READY = "ready"
    COMPLETED = "completed"
    REJECTED = "rejected"

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    customer_wa_id: Mapped[str] = mapped_column(String(50), index=True)
    customer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    state: Mapped[str] = mapped_column(String(50), default="open")
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_live_takeover: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    company = relationship("Company", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(20))  # inbound | outbound
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation = relationship("Conversation", back_populates="messages")

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    customer_wa_id: Mapped[str] = mapped_column(String(50))
    customer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(10), default="NGN")
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.AWAITING_ACCEPTANCE)
    payment_proof_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    company = relationship("Company", back_populates="orders")
