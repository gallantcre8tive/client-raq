from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(40), default="per piece")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    company = relationship("Company", back_populates="services")
