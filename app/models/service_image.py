from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ServiceExampleImage(Base):
    __tablename__ = "service_example_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[str | None] = mapped_column(String(500), nullable=True)  # comma-separated
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
