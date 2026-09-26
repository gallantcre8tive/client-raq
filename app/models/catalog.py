from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, Float, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Service(Base):
    """Company-enabled service. pricing_method:
    piece | sqft | sqin | tier | setup_unit | custom | fixed_size
    fixed_size = use ServiceVariant rows (frames, nylon sizes, etc.)
    """
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(40), default="per piece")
    pricing_method: Mapped[str] = mapped_column(String(30), default="piece")
    catalog_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    flow_type: Mapped[str] = mapped_column(String(40), default="generic")
    # generic | frame | nylon | garment | large_format | paper | book
    design_fee: Mapped[float] = mapped_column(Float, default=0)
    min_qty: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    company = relationship("Company", back_populates="services")
    variants = relationship("ServiceVariant", back_populates="service", cascade="all, delete-orphan")


class ServiceVariant(Base):
    """Fixed size / option row e.g. Frame 8x10 = 3500."""
    __tablename__ = "service_variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))  # e.g. 8x10
    subtitle: Mapped[str | None] = mapped_column(String(120), nullable=True)  # e.g. inches
    price: Mapped[float] = mapped_column(Float, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    service = relationship("Service", back_populates="variants")
