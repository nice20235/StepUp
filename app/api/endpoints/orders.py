from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderItemCreate,
    OrderFromCartRequest,
)
from app.crud.order import (
    get_orders,
    get_user_orders,
    get_order,
    create_order,
    update_order,
    delete_order,
)
from app.models.order import OrderItem
from app.models.stepup import StepUp
from app.core.timezone import to_tashkent, format_tashkent_compact
from app.auth.dependencies import get_current_user, get_current_admin
import logging

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/from-cart")
async def create_order_from_cart(
    payload: OrderFromCartRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    clear_cart: bool = Query(False, description="If true, clears cart after creating the order"),
):
    """Create a new order from the current user's cart items.
    If `clear_cart=true` is provided, the user's cart will be emptied after order creation.
    """
    from app.crud.cart import get_cart, get_cart_totals, clear_cart as clear_cart_fn
    from app.schemas.order import OrderCreate, OrderItemCreate

    # Basic validation of client-provided amount
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    cart = await get_cart(db, user.id)
    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Validate that provided cart_id matches current user's cart public id
    public_cart_id = f"cart_{cart.id}"
    if payload.cart_id != public_cart_id:
        raise HTTPException(status_code=400, detail="cart_id does not match current user's cart")

    # Ensure client-provided amount matches server-side cart total (in UZS)
    _, _, cart_total = await get_cart_totals(db, user.id)
    cart_total_int = int(cart_total or 0)
    if cart_total_int <= 0:
        raise HTTPException(status_code=400, detail="Cart total is zero")
    if payload.amount != cart_total_int:
        raise HTTPException(status_code=400, detail="Amount does not match cart total")

    # Build items from cart lines; unit_price is determined from DB at creation time
    items_source: list[OrderItemCreate] = [
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
        items=items_source,
        notes=None,
    )
    try:
        new_order = await create_order(
            db,
            internal_order,
            idempotency_key=None,
            merge_fallback=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # At this point, cart_total has already been checked against payload.amount,
    # so we can safely trust it for total_amount.
    new_order.total_amount = float(cart_total_int)
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)

    # Optionally clear cart after order creation (opt-in)
    if clear_cart:
        try:
            await clear_cart_fn(db, user.id)
        except Exception as e:
            logger.warning("Failed to clear cart after order creation: %s", e)

    # Invalidate caches
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("orders:")

    created_compact = format_tashkent_compact(new_order.created_at)
    # Explicitly fetch items for response
    items_result = await db.execute(
        select(OrderItem, StepUp)
        .join(StepUp, StepUp.id == OrderItem.slipper_id)
        .where(OrderItem.order_id == new_order.id)
        .order_by(OrderItem.id.asc())
    )
    items = items_result.all()
    return {
        "order_id": new_order.order_id,
        "status": new_order.status.value if hasattr(new_order.status, 'value') else str(new_order.status),
        "total_amount": new_order.total_amount,
        "notes": new_order.notes,
        "created_at": created_compact,
        "items": [
            {
                "slipper_id": oi.slipper_id,
                "quantity": oi.quantity,
                "unit_price": oi.unit_price,
                "total_price": oi.total_price,
                "notes": oi.notes,
                "name": getattr(sl, "name", None),
                "size": getattr(sl, "size", None),
                "image": getattr(sl, "image", None),
            }
            for oi, sl in items
        ],
    }

@router.get("/")
async def list_orders(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List orders.
    - Regular user: only their own orders.
    - Admin: all users' orders.
    Pagination & status filtering removed per request.
    """
    try:
        # Build a per-user cache key shim by referencing args in cached decorator
        _ = user.id  # referenced for clarity; no-op
        if user.is_admin:
            logger.info(f"Admin {user.name} listing ALL orders")
            orders, total = await get_orders(db, skip=0, limit=100000, load_relationships=False)
        else:
            logger.info(f"User {user.name} listing OWN orders")
            orders, total = await get_orders(db, skip=0, limit=100000, user_id=user.id, load_relationships=False)

        # Batch load users and items for these orders to avoid N+1
        order_ids = [o.id for o in orders]
        user_ids = list({o.user_id for o in orders})
        users_by_id: dict[int, tuple[str, str]] = {}
        if user_ids:
            from app.models.user import User as _User
            user_rows = await db.execute(select(_User.id, _User.name, _User.surname).where(_User.id.in_(user_ids)))
            for uid, nm, sn in user_rows.all():
                users_by_id[int(uid)] = (nm, sn)

        items_by_order: dict[int, list[dict]] = {}
        if order_ids:
            data = await db.execute(
                select(OrderItem, StepUp)
                .join(StepUp, StepUp.id == OrderItem.slipper_id)
                .where(OrderItem.order_id.in_(order_ids))
            )
            for oi, sl in data.all():
                items_by_order.setdefault(oi.order_id, []).append({
                    "slipper_id": oi.slipper_id,
                    "quantity": oi.quantity,
                    "unit_price": oi.unit_price,
                    "total_price": oi.total_price,
                    "name": getattr(sl, "name", None),
                    "size": getattr(sl, "size", None),
                    "image": getattr(sl, "image", None),
                })

        return [
            {
                "id": order.id,
                "order_id": order.order_id,
                "user_id": order.user_id,
                "user_name": (f"{users_by_id[order.user_id][0]} {users_by_id[order.user_id][1]}".strip() if order.user_id in users_by_id else None),
                "status": order.status.value,
                "total_amount": order.total_amount,
                "created_at": format_tashkent_compact(order.created_at),
                "updated_at": format_tashkent_compact(order.updated_at),
                "items": items_by_order.get(order.id, []),
            }
            for order in orders
        ]
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        raise HTTPException(status_code=500, detail="Error fetching orders")

@router.get("/{order_id}")
async def get_order_endpoint(
    order_id: int, 
    db: AsyncSession = Depends(get_db), 
    user=Depends(get_current_user)
):
    """Get a specific order by ID"""
    try:
        db_order = await get_order(db, order_id, load_relationships=False)
        if not db_order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Check permissions
        if not user.is_admin and db_order.user_id != user.id:
            logger.warning(f"User {user.name} attempted to access order {order_id} belonging to user {db_order.user_id}")
            raise HTTPException(status_code=403, detail="Not authorized to access this order")
        
        # Fetch items explicitly to avoid any lazy-load issues and ensure correctness
        items_data = await db.execute(
            select(OrderItem, StepUp)
            .join(StepUp, StepUp.id == OrderItem.slipper_id)
            .where(OrderItem.order_id == db_order.id)
            .order_by(OrderItem.id.asc())
        )
        items = [
            {
                "id": oi.id,
                "slipper_id": oi.slipper_id,
                "quantity": oi.quantity,
                "unit_price": oi.unit_price,
                "total_price": oi.total_price,
                "notes": oi.notes,
                "name": getattr(sl, "name", None),
                "size": getattr(sl, "size", None),
                "image": getattr(sl, "image", None),
            }
            for oi, sl in items_data.all()
        ]

        return {
            "id": db_order.id,
            "order_id": db_order.order_id,
            "user_id": db_order.user_id,
            "status": db_order.status.value if hasattr(db_order.status, 'value') else str(db_order.status),
            "total_amount": db_order.total_amount,
            "notes": db_order.notes,
            "created_at": format_tashkent_compact(db_order.created_at),
            "updated_at": format_tashkent_compact(db_order.updated_at),
            "items": items,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {e}")
        raise HTTPException(status_code=500, detail="Error fetching order")

@router.put("/{order_id}")
async def update_order_endpoint(order_id: int, order_update: OrderUpdate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """
    Update an order. Admins can update any order, users can only update their own orders.
    """
    db_order = await get_order(db, order_id)
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Check permissions
    if not user.is_admin and db_order.user_id != user.id:
        logger.warning(f"User {user.name} (Admin: {user.is_admin}) attempted to update order {order_id} belonging to user {db_order.user_id}")
        raise HTTPException(
            status_code=403, 
            detail="You can only update your own orders"
        )
    
    logger.info(f"Updating order {order_id} by user: {user.name} (Admin: {user.is_admin})")
    updated_order = await update_order(db, db_order, order_update)

    # Clear cache after updating order
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("orders:")
    await invalidate_cache_pattern(f"order:{order_id}:")

    # Explicitly reload items for this order to avoid any async lazy-load pitfalls
    items_data = await db.execute(
        select(OrderItem, StepUp)
        .join(StepUp, StepUp.id == OrderItem.slipper_id)
        .where(OrderItem.order_id == updated_order.id)
        .order_by(OrderItem.id.asc())
    )
    items = [
        {
            "id": oi.id,
            "slipper_id": oi.slipper_id,
            "quantity": oi.quantity,
            "unit_price": oi.unit_price,
            "total_price": oi.total_price,
            "notes": oi.notes,
            "name": getattr(sl, "name", None),
            "size": getattr(sl, "size", None),
            "image": getattr(sl, "image", None),
        }
        for oi, sl in items_data.all()
    ]

    return {
        "id": updated_order.id,
        "order_id": updated_order.order_id,
        "user_id": updated_order.user_id,
        "status": updated_order.status.value if hasattr(updated_order.status, 'value') else str(updated_order.status),
        "total_amount": updated_order.total_amount,
        "notes": updated_order.notes,
        "created_at": format_tashkent_compact(updated_order.created_at),
        "updated_at": format_tashkent_compact(updated_order.updated_at),
        "items": items,
    }

@router.delete("/{order_id}")
async def delete_order_endpoint(order_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """
    Delete an order. Admins can delete any order, users can only delete their own orders.
    """
    db_order = await get_order(db, order_id)
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Check permissions
    if not user.is_admin and db_order.user_id != user.id:
        logger.warning(f"User {user.name} (Admin: {user.is_admin}) attempted to delete order {order_id} belonging to user {db_order.user_id}")
        raise HTTPException(
            status_code=403, 
            detail="You can only delete your own orders"
        )
    
    logger.info(f"Deleting order {order_id} by user: {user.name} (Admin: {user.is_admin})")
    await delete_order(db, db_order)
    
    # Clear cache after deleting order
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("orders:")
    await invalidate_cache_pattern(f"order:{order_id}:")
    
    return {"msg": "Order deleted"} 