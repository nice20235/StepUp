from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.payment import Payment, PaymentStatus
from typing import Optional


async def create_payment(
    db: AsyncSession,
    *,
    shop_transaction_id: str,
    amount: float,
    currency: str,
    order_id: Optional[int] = None,
    octo_payment_uuid: Optional[str] = None,
) -> Payment:
    payment = Payment(
        shop_transaction_id=shop_transaction_id,
        amount=amount,
        currency=currency,
        order_id=order_id,
        status=PaymentStatus.CREATED,
        octo_payment_uuid=octo_payment_uuid,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


async def get_payment_by_shop_tx(db: AsyncSession, shop_transaction_id: str) -> Optional[Payment]:
    result = await db.execute(select(Payment).where(Payment.shop_transaction_id == shop_transaction_id))
    return result.scalar_one_or_none()


async def get_payment_by_uuid(db: AsyncSession, octo_payment_uuid: str) -> Optional[Payment]:
    result = await db.execute(select(Payment).where(Payment.octo_payment_uuid == octo_payment_uuid))
    return result.scalar_one_or_none()


async def update_payment_status(
    db: AsyncSession,
    payment: Payment,
    *,
    status: PaymentStatus,
    octo_payment_uuid: Optional[str] = None,
    raw: Optional[str] = None,
) -> Payment:
    payment.status = status
    if octo_payment_uuid:
        payment.octo_payment_uuid = octo_payment_uuid
    if raw:
        payment.raw = raw
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment
