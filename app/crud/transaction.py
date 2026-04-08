from __future__ import annotations

from typing import Optional, Sequence
from sqlalchemy import select, and_, between
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


async def get_by_acquirer_id(db: AsyncSession, acquirer_id: str) -> Optional[Transaction]:
    stmt = select(Transaction).where(Transaction.id == acquirer_id)
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def create_transaction(
    db: AsyncSession,
    *,
    acquirer_id: str,
    merchant_transaction_id: str,
    amount: int,
    state: int,
    create_time: int,
    account_data: dict,
    perform_time: int = 0,
    cancel_time: int = 0,
    reason: Optional[int] = None,
) -> Transaction:
    tx = Transaction(
        id=acquirer_id,
        transaction=merchant_transaction_id,
        amount=amount,
        state=state,
        create_time=create_time,
        perform_time=perform_time,
        cancel_time=cancel_time,
        account_data=account_data,
        reason=reason,
    )
    db.add(tx)
    await db.flush()
    await db.refresh(tx)
    return tx


async def update_transaction_state(
    db: AsyncSession,
    *,
    tx: Transaction,
    state: int,
    perform_time: Optional[int] = None,
    cancel_time: Optional[int] = None,
    reason: Optional[int] = None,
) -> Transaction:
    tx.state = state
    if perform_time is not None:
        tx.perform_time = perform_time
    if cancel_time is not None:
        tx.cancel_time = cancel_time
    if reason is not None:
        tx.reason = reason
    await db.flush()
    await db.refresh(tx)
    return tx


async def get_statement(
    db: AsyncSession,
    *,
    from_time: int,
    to_time: int,
) -> Sequence[Transaction]:
    stmt = (
        select(Transaction)
        .where(
            and_(
                Transaction.create_time >= from_time,
                Transaction.create_time <= to_time,
            )
        )
        .order_by(Transaction.create_time)
    )
    res = await db.execute(stmt)
    return res.scalars().all()
