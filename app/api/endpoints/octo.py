import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict
from app.auth.dependencies import get_current_user, get_current_admin
from app.services.octo import createPayment, refundPayment
from app.db.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from app.crud.payment import create_payment, get_payment_by_uuid, get_payment_by_shop_tx, update_payment_status
from app.models.payment import PaymentStatus
from app.crud.order import update_order_status
from app.models.order import OrderStatus
from app.core.cache import invalidate_cache_pattern

router = APIRouter()
logger = logging.getLogger(__name__)

class OctoCreateIn(BaseModel):
    # Accept multiple aliases from clients
    order_id: Optional[int] = Field(None, ge=1, description="Order ID")
    orderId: Optional[int] = Field(None, ge=1, description="Order ID alias")
    # Optional: allow clients to send items directly (preferred over cart fallback)
    items: Optional[list[dict]] = Field(None, description="Items to create order from if order_id not provided")
    # Optional amount passthrough (backend still uses order.total_amount by default)
    amount: Optional[int] = Field(None, ge=1)
    total_sum: Optional[int] = Field(None, ge=1)
    model_config = {
        "json_schema_extra": {
            "example": {"order_id": 2}
        }
    }

class OctoCreateOut(BaseModel):
    order_id: int
    redirect_url: str
    payment_uuid: Optional[str] = Field(None, description="Payment UUID from bank (external gateway)")

class OctoRefundIn(BaseModel):
    order_id: int = Field(..., ge=1, description="Order ID")
    model_config = {"json_schema_extra": {"example": {"order_id": 2}}}

class OctoRefundOut(BaseModel):
    order_id: int
    status: str

@router.post("/create", response_model=OctoCreateOut, summary="Create Octo Payment", responses={
    200: {
        "description": "Ссылка на оплату создана",
        "content": {
            "application/json": {
                "example": {
                    "pay_url": "https://octo.uz/pay/abcd...",
                    "payment_uuid": "f4f28a3e-3b60-4a3a-8c2e-0e9f7a1e8b05"
                }
            }
        }
    },
    400: {
        "description": "Ошибка OCTO",
        "content": {
            "application/json": {
                "example": {"detail": "Merchant status [emailok] is not allowed for non-test Payments."}
            }
        }
    }
})
async def create_octo_payment(body: OctoCreateIn, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create a payment for an order and return redirect_url plus payment_uuid.
    Now returns payment_uuid per updated requirement so client can track it.
    """
    from app.crud.order import get_order, update_order_payment_uuid
    # Normalize order_id from possible aliases
    oid = body.order_id or body.orderId
    # If no order provided, create an order from provided items first, else from cart (pre-payment)
    if not oid:
        from app.schemas.order import OrderCreate, OrderItemCreate
        from app.crud.order import create_order
        from app.crud.cart import get_cart, get_cart_totals
        internal_items: list[OrderItemCreate] | None = None
        if body.items:
            try:
                internal_items = [
                    OrderItemCreate(
                        slipper_id=int(it.get("slipper_id")),
                        quantity=int(it.get("quantity", 1)),
                        unit_price=1.0,
                        notes=it.get("notes"),
                    )
                    for it in body.items
                    if it and it.get("slipper_id")
                ]
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid items format")
        if not internal_items:
            cart = await get_cart(db, user.id)
            if not cart or not cart.items:
                raise HTTPException(status_code=400, detail="Cart is empty")
            internal_items = [
                OrderItemCreate(
                    slipper_id=ci.slipper_id,
                    quantity=ci.quantity,
                    unit_price=1.0,
                    notes=None,
                )
                for ci in cart.items
            ]
        internal_order = OrderCreate(
            order_id=None,
            user_id=user.id,
            items=internal_items,
            notes=None,
        )
        created = await create_order(db, internal_order, idempotency_key=None, merge_fallback=False)
        # Align total to cart total
        _, _, cart_total = await get_cart_totals(db, user.id)
        if cart_total and abs((created.total_amount or 0.0) - cart_total) > 0.5:
            created.total_amount = float(cart_total)
            db.add(created)
            await db.commit()
            await db.refresh(created)
        oid = created.id
    order = await get_order(db, int(oid), load_relationships=False)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # Enforce ownership
    if order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Order does not belong to current user")
    # Ensure the current user owns the order
    if order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not allowed to pay for this order")
    if order.status == OrderStatus.REFUNDED:
        raise HTTPException(status_code=400, detail="Cannot create payment for refunded order")
    # Idempotency: if order already has a payment_uuid and still pending, just reuse
    if order.payment_uuid:
        existing_payment_uuid = order.payment_uuid
        # Try find existing payment record to reconstruct redirect (cannot fetch if not stored; return 409 style?)
        # For simplicity, continue to allow new creation if user explicitly wants another attempt and old payment maybe failed.
        logger.info("Order %s already has payment_uuid %s", order.id, existing_payment_uuid)
    # Allow optional override if provided (defers to order.total_amount otherwise)
    override_amount = body.amount or body.total_sum
    amount = int(round(order.total_amount)) if not override_amount else int(override_amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Order total is zero; cannot create payment")
    # Mock external OCTO call via existing service wrapper; fallback fabricate
    res = await createPayment(amount, f"Order #{order.order_id}")
    if not res.success or not res.octo_pay_url:
        raise HTTPException(status_code=400, detail=res.errMessage or "OCTO error")
    # Persist to payments table (internal tracking) & attach payment_uuid to order
    if res.shop_transaction_id:
        try:
            await create_payment(
                db,
                shop_transaction_id=res.shop_transaction_id,
                amount=float(amount),
                currency="UZS",
                order_id=order.id,
                octo_payment_uuid=res.octo_payment_UUID,
            )
        except Exception as e:
            logger.warning("Failed to persist payment row: %s", e)
    payment_uuid = res.octo_payment_UUID
    if not payment_uuid and res.raw:
        # Try to extract from nested data
        data_section = res.raw.get("data") or {}
        payment_uuid = data_section.get("octo_payment_UUID") or data_section.get("payment_uuid")
    if not payment_uuid:
        logger.warning("OCTO response missing payment UUID for order %s", order.id)
    if payment_uuid and not order.payment_uuid:
        try:
            await update_order_payment_uuid(db, order.id, payment_uuid)
        except Exception as e:
            logger.warning("Failed to store payment_uuid on order %s: %s", order.id, e)
    return OctoCreateOut(order_id=order.id, redirect_url=res.octo_pay_url, payment_uuid=payment_uuid)

@router.post("/refund", response_model=OctoRefundOut, summary="Refund Octo Payment", responses={
    200: {
        "description": "Возврат инициирован",
        "content": {"application/json": {"example": {"success": True, "message": "Refund initiated"}}}
    },
    400: {
        "description": "Ошибка OCTO",
        "content": {"application/json": {"example": {"detail": "Minimum refund is >= 13000 UZS (1 USD)"}}}
    }
})
async def refund_octo_payment(body: OctoRefundIn, admin=Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    from app.crud.order import get_order, update_order_status
    order = await get_order(db, body.order_id, load_relationships=False)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == OrderStatus.REFUNDED:
        raise HTTPException(status_code=400, detail="Order already refunded")
    if not order.payment_uuid:
        raise HTTPException(status_code=400, detail="Order has no payment_uuid; cannot refund")
    # Mock external refund call
    res = await refundPayment(order.payment_uuid, int(round(order.total_amount)))
    if not res.success:
        raise HTTPException(status_code=400, detail=res.errMessage or "OCTO refund error")
    await update_order_status(db, order.id, OrderStatus.REFUNDED)
    return OctoRefundOut(order_id=order.id, status=OrderStatus.REFUNDED.value)

# Optional: minimal notify webhook to satisfy notify_url requirement
class OctoNotifyIn(BaseModel):
    payment_uuid: Optional[str] = None
    shop_transaction_id: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[int] = None
    extra: Dict[str, Any] | None = None
    model_config = {
        "json_schema_extra": {
            "example": {
                "shop_transaction_id": "<from_create_payment_response>",
                "status": "paid"
            }
        }
    }

@router.post("/notify", summary="Octo notify webhook")
async def octo_notify(request: Request, db: AsyncSession = Depends(get_db)):
    # Try to get JSON payload first, fallback to form data or empty dict
    payload = {}
    body_data = None
    
    try:
        # Try JSON first
        payload = await request.json()
    except Exception:
        try:
            # Try form data
            form_data = await request.form()
            payload = dict(form_data)
        except Exception:
            # Try raw body as backup
            try:
                body_bytes = await request.body()
                if body_bytes:
                    import json
                    payload = json.loads(body_bytes.decode('utf-8'))
            except Exception:
                payload = {}
    
    logger.info("OCTO notify received: %s", payload)
    # TODO: verify signature if OCTO provides one
    shop_tx = payload.get("shop_transaction_id")
    payment_uuid = payload.get("payment_uuid")
    status = (payload.get("status") or "").lower()
    # Load payment record by shop_transaction_id first
    payment = None
    if shop_tx:
        payment = await get_payment_by_shop_tx(db, shop_tx)
    if not payment and payment_uuid:
        payment = await get_payment_by_uuid(db, payment_uuid)
    if not payment:
        logger.warning("Payment record not found for notify: shop_tx=%s uuid=%s", shop_tx, payment_uuid)
        return {"ok": True}

    # If payment is not linked to an order, try to bind from payload (best-effort)
    if getattr(payment, "order_id", None) is None:
        try:
            maybe_order_id = (
                payload.get("order_id")
                or payload.get("orderId")
                or (payload.get("extra") or {}).get("orderId")
            )
            if maybe_order_id is not None:
                payment.order_id = int(maybe_order_id)
                db.add(payment)
                await db.commit()
                await db.refresh(payment)
                logger.info("Linked payment %s to order_id=%s from notify payload", payment.id, payment.order_id)
        except Exception as e:
            logger.warning("Failed to link order_id from notify payload: %s", e)
    # Map external status to internal
    mapped = None
    # Normalize and map a broader set of success statuses
    if status in {"paid", "success", "succeeded", "paid_and_captured", "captured", "completed"}:
        mapped = PaymentStatus.PAID
    elif status in {"refunded", "refund"}:
        mapped = PaymentStatus.REFUNDED
    elif status in {"failed", "error"}:
        mapped = PaymentStatus.FAILED
    elif status in {"cancelled", "canceled"}:
        mapped = PaymentStatus.CANCELLED
    else:
        mapped = PaymentStatus.PENDING

    logger.info(
        "Notify status '%s' mapped to %s (payment_id=%s, order_id=%s)",
        status,
        mapped,
        getattr(payment, "id", None),
        getattr(payment, "order_id", None),
    )
    updated = await update_payment_status(
        db,
        payment,
        status=mapped,
        octo_payment_uuid=payment_uuid,
        raw=str(payload)[:3900],
    )
    # Update order status based on payment lifecycle events
    if getattr(updated, "order_id", None):
        if mapped == PaymentStatus.PAID:
            try:
                await update_order_status(db, updated.order_id, OrderStatus.PAID)
                logger.info("Order %s set to PAID due to successful payment %s", updated.order_id, updated.id)
                # Clear the purchaser's cart after successful payment
                try:
                    from app.crud.order import get_order as _get_order
                    from app.crud.cart import clear_cart as _clear_cart
                    paid_order = await _get_order(db, updated.order_id, load_relationships=False)
                    if paid_order:
                        await _clear_cart(db, paid_order.user_id)
                except Exception as e:
                    logger.warning("Failed to clear cart after payment: %s", e)
            except Exception as e:
                logger.warning("Failed to update order %s status after payment: %s", updated.order_id, e)
        elif mapped == PaymentStatus.REFUNDED:
            try:
                await update_order_status(db, updated.order_id, OrderStatus.REFUNDED)
                logger.info("Order %s set to REFUNDED due to refund of payment %s", updated.order_id, updated.id)
            except Exception as e:
                logger.warning("Failed to update order %s status after refund: %s", updated.order_id, e)
    else:
        if mapped == PaymentStatus.PAID:
            logger.warning(
                "Payment %s marked PAID but not linked to any order. Ensure orderId is sent on create.",
                getattr(updated, "id", None),
            )
    # Invalidate caches so clients don't see stale 'pending' orders
    try:
        await invalidate_cache_pattern("orders:")
        if getattr(updated, "order_id", None):
            await invalidate_cache_pattern(f"order:{updated.order_id}:")
    except Exception:
        pass
    return {"ok": True}
