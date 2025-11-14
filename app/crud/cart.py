from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.models.cart import Cart, CartItem
from app.models.stepup import StepUp
from app.schemas.cart import CartItemCreate, CartItemUpdate
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Helper to load a cart with items + slippers eagerly
_cart_with_items = lambda: [selectinload(Cart.items).selectinload(CartItem.slipper)]  # noqa: E731

async def _reload_cart(db: AsyncSession, cart_id: int) -> Cart:
    q = await db.execute(
        select(Cart)
        .where(Cart.id == cart_id)
        .options(*_cart_with_items())
    )
    return q.scalar_one()

async def get_or_create_cart(db: AsyncSession, user_id: int) -> Cart:
    # Deterministically pick the oldest cart if duplicates exist (data repair may still be running)
    q = await db.execute(
        select(Cart)
        .where(Cart.user_id == user_id)
        .order_by(Cart.id.asc())
        .options(*_cart_with_items())
    )
    cart = q.scalar_one_or_none()
    if cart:
        return cart
    cart = Cart(user_id=user_id)
    db.add(cart)
    await db.commit()
    return await _reload_cart(db, cart.id)

async def get_cart(db: AsyncSession, user_id: int) -> Optional[Cart]:
    q = await db.execute(
        select(Cart)
        .where(Cart.user_id == user_id)
        .options(*_cart_with_items())
    )
    return q.scalar_one_or_none()

async def add_item(db: AsyncSession, user_id: int, item: CartItemCreate) -> Cart:
    cart = await get_or_create_cart(db, user_id)
    # Ensure stepup exists
    slipper = (await db.execute(select(StepUp).where(StepUp.id == item.slipper_id))).scalar_one_or_none()
    if not slipper:
        raise ValueError("StepUp not found")
    # Merge or add with stock check
    existing_qty = 0
    for ci in cart.items:
        if ci.slipper_id == item.slipper_id:
            existing_qty = ci.quantity
            new_qty = existing_qty + int(item.quantity)
            if new_qty > (slipper.quantity or 0):
                raise ValueError(
                    f"Requested quantity exceeds available stock (requested={new_qty}, available={slipper.quantity})"
                )
            ci.quantity = new_qty
            db.add(ci)
            break
    else:
        # brand new line
        req = int(item.quantity)
        if req > (slipper.quantity or 0):
            raise ValueError(
                f"Requested quantity exceeds available stock (requested={req}, available={slipper.quantity})"
            )
        db.add(CartItem(cart_id=cart.id, slipper_id=item.slipper_id, quantity=req))
    await db.commit()
    return await _reload_cart(db, cart.id)

async def update_item(db: AsyncSession, user_id: int, cart_item_id: int, update: CartItemUpdate) -> Cart:
    cart = await get_or_create_cart(db, user_id)
    target = next((ci for ci in cart.items if ci.id == cart_item_id), None)
    if not target:
        raise ValueError("Cart item not found")
    if update.quantity == 0:
        await db.delete(target)
    else:
        # Ensure we have slipper loaded and stock is sufficient
        slipper = target.slipper
        if slipper is None:
            slipper = (await db.execute(select(StepUp).where(StepUp.id == target.slipper_id))).scalar_one_or_none()
        req = int(update.quantity)
        if req > (slipper.quantity or 0):
            raise ValueError(
                f"Requested quantity exceeds available stock (requested={req}, available={slipper.quantity})"
            )
        target.quantity = req
        db.add(target)
    await db.commit()
    return await _reload_cart(db, cart.id)

async def remove_item(db: AsyncSession, user_id: int, cart_item_id: int) -> Cart:
    cart = await get_or_create_cart(db, user_id)
    target = next((ci for ci in cart.items if ci.id == cart_item_id), None)
    if not target:
        raise ValueError("Cart item not found")
    await db.delete(target)
    await db.commit()
    return await _reload_cart(db, cart.id)

async def clear_cart(db: AsyncSession, user_id: int) -> Cart:
    cart = await get_or_create_cart(db, user_id)
    for ci in list(cart.items):
        await db.delete(ci)
    await db.commit()
    return await _reload_cart(db, cart.id)


async def get_cart_totals(db: AsyncSession, user_id: int) -> tuple[int, int, float]:
    """Efficiently compute totals for a user's cart via SQL aggregation.
    Returns: (total_items, total_quantity, total_amount)
    total_items = number of distinct cart lines
    total_quantity = sum of quantities across lines
    total_amount = sum(slipper.price * quantity) across lines
    """
    # Subquery: cart for user
    cart_q = await db.execute(select(Cart.id).where(Cart.user_id == user_id))
    cart_id = cart_q.scalar_one_or_none()
    if cart_id is None:
        return 0, 0, 0.0

    q = (
        select(
            func.count(CartItem.id),
            func.coalesce(func.sum(CartItem.quantity), 0),
            func.coalesce(func.sum((StepUp.price * CartItem.quantity)), 0.0),
        )
        .join(StepUp, StepUp.id == CartItem.slipper_id)
        .where(CartItem.cart_id == cart_id)
    )
    res = await db.execute(q)
    total_items, total_quantity, total_amount = res.first() or (0, 0, 0.0)
    # Ensure Python types
    total_items = int(total_items or 0)
    total_quantity = int(total_quantity or 0)
    total_amount = float(total_amount or 0.0)
    return total_items, total_quantity, total_amount

