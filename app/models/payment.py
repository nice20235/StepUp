from sqlalchemy import String, Integer, Float, DateTime, func, ForeignKey, Enum, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
from datetime import datetime
import enum


class PaymentStatus(str, enum.Enum):
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    shop_transaction_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    octo_payment_uuid: Mapped[str | None] = mapped_column(String(64), unique=False, nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="UZS")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.CREATED, nullable=False, index=True)
    raw: Mapped[str | None] = mapped_column(String(4000), nullable=True)  # store compact json string if needed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_payments_order_status", "order_id", "status"),
        Index("idx_payments_created", "created_at"),
    )
