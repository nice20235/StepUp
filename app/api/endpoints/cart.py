import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.auth.dependencies import get_current_user
from app.schemas.cart import CartItemCreate, CartItemUpdate, CartOut, CartItemOut, CartTotalOut
from app.crud.cart import get_or_create_cart, add_item, update_item, remove_item, clear_cart, get_cart, get_cart_totals

logger = logging.getLogger(__name__)

# Use a single, consistently-capitalized tag name to avoid duplicate sections in the docs
router = APIRouter(prefix="/cart", tags=["Cart"])


def _serialize(cart) -> CartOut:
    total_items = len(cart.items)
    total_quantity = sum(ci.quantity for ci in cart.items)
    # Attempt to enrich with slipper data (if loaded later we can optimize)
    items_out = []
    total_amount = 0.0
    for ci in cart.items:
        name = None
        price = None
        total_price = None
        if getattr(ci, 'slipper', None):
            name = ci.slipper.name
            price = ci.slipper.price
            total_price = price * ci.quantity
            total_amount += total_price
        items_out.append(CartItemOut(
            id=ci.id,
            slipper_id=ci.slipper_id,
            quantity=ci.quantity,
            name=name,
            price=price,
            total_price=total_price
        ))
    return CartOut(id=cart.id, items=items_out, total_items=total_items, total_quantity=total_quantity, total_amount=total_amount)

@router.get("", response_model=CartOut)
async def get_my_cart(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, user.id)
    return _serialize(cart)

@router.get("/total", response_model=CartTotalOut)
async def get_my_cart_total(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Return only the totals for the current user's cart."""
    total_items, total_quantity, total_amount = await get_cart_totals(db, user.id)
    return CartTotalOut(total_items=total_items, total_quantity=total_quantity, total_amount=total_amount)

@router.post("/items", response_model=CartOut)
async def add_cart_item(payload: CartItemCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        cart = await add_item(db, user.id, payload)
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)
    return _serialize(cart)

@router.put("/items/{cart_item_id}", response_model=CartOut)
async def update_cart_item(cart_item_id: int, payload: CartItemUpdate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        cart = await update_item(db, user.id, cart_item_id, payload)
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)
    return _serialize(cart)

@router.delete("/items/{cart_item_id}", response_model=CartOut)
async def delete_cart_item(cart_item_id: int, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        cart = await remove_item(db, user.id, cart_item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _serialize(cart)

@router.delete("/clear", response_model=CartOut)
async def clear_my_cart(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    cart = await clear_cart(db, user.id)
    return _serialize(cart)

