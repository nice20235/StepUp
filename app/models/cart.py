from sqlalchemy import Integer, ForeignKey, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.db.database import Base
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.slipper import Slipper

class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="carts")
    items: Mapped[list["CartItem"]] = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_carts_user", "user_id"),
        Index("idx_carts_created", "created_at"),
    )

class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cart_id: Mapped[int] = mapped_column(Integer, ForeignKey("carts.id", ondelete="CASCADE"), index=True)
    slipper_id: Mapped[int] = mapped_column(Integer, ForeignKey("slippers.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cart: Mapped[Cart] = relationship("Cart", back_populates="items")
    slipper: Mapped["Slipper"] = relationship("Slipper")

    __table_args__ = (
        Index("idx_cart_items_cart", "cart_id"),
        Index("idx_cart_items_slipper", "slipper_id"),
    )
