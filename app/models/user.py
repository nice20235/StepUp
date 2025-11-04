from sqlalchemy import String, Integer, Boolean, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.cart import Cart

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    surname: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    # Relationships
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    carts: Mapped[list["Cart"]] = relationship("Cart", back_populates="user", cascade="all, delete-orphan")
    
    # Indexes for better query performance
    __table_args__ = (
        Index('idx_users_name', 'name'),
        Index('idx_users_surname', 'surname'),
        Index('idx_users_phone_number', 'phone_number'),
        Index('idx_users_admin', 'is_admin'),
        Index('idx_users_created_at', 'created_at'),
        Index('idx_users_name_surname', 'name', 'surname'),  # Composite for full name search
        Index('idx_users_admin_created', 'is_admin', 'created_at'),  # For admin queries with date
    )
    
    def __repr__(self):
        return f"<User(id={self.id}, name='{self.name}', surname='{self.surname}', phone_number='{self.phone_number}', is_admin={self.is_admin})>" 