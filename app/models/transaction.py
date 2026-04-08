from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Integer, String, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Transaction(Base):
    """Payment transaction model for acquirer<->merchant integration.

    Fields strictly follow the specification:
    - id: identifier from the acquirer side (string)
    - transaction: identifier on merchant side (string)
    - amount: amount in tiyin (1 UZS = 100 tiyin), stored as bigint
    - state: transaction state (1=created, 2=paid, -2=canceled)
    - create_time: creation Unix time in milliseconds
    - perform_time: perform Unix time in milliseconds (or 0 if not performed)
    - cancel_time: cancel Unix time in milliseconds (or 0 if not cancelled)
    - account_data: raw account object from request (JSON)
    - reason: cancel reason code (integer, nullable)
    """

    __tablename__ = "transactions"

    # Internal numeric PK for DB performance
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Acquirer transaction identifier (required, unique)
    id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Merchant transaction identifier (string, required)
    transaction: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Amount in tiyin (1 UZS = 100 tiyin)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Transaction state: 1=created, 2=paid, -2=canceled
    state: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Timestamps are stored as Unix time in milliseconds, as required by spec
    create_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    perform_time: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancel_time: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Original account object from requests
    account_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Cancel reason (integer code from acquirer), nullable
    reason: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        Index("idx_transactions_state", "state"),
        Index("idx_transactions_times", "create_time", "perform_time", "cancel_time"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Transaction(id={self.id!r}, transaction={self.transaction!r}, "
            f"amount={self.amount}, state={self.state})>"
        )
