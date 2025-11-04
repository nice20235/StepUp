from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderCreatePublic,
    OrderItemCreate,
)
from app.crud.order import (
    get_orders,
    get_user_orders,
    get_order,
    create_order,
    update_order,
    delete_order,
    get_orders_by_payment_statuses,
)
from app.models.payment import PaymentStatus
from app.models.order import OrderItem
from app.models.slipper import Slipper
from app.core.timezone import to_tashkent, format_tashkent_compact
from app.auth.dependencies import get_current_user, get_current_admin
import logging

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/")
async def create_order_endpoint(
    order: OrderCreatePublic,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    x_merge_with_latest: str | None = Header(default=None, alias="X-Merge-With-Latest"),
):
    """
    Create a new order. Available to all authenticated users.
    """
    logger.info(f"Creating order for user: {user.name} (Admin: {user.is_admin})")
    # Require explicit payload items; do not auto-fallback to cart here to avoid accidental merges
    items_source: list[OrderItemCreate]
    if getattr(order, "items", None):
        items_source = [
            OrderItemCreate(
                slipper_id=it.slipper_id,
                quantity=it.quantity,
                unit_price=1.0,
                notes=it.notes,
            )
            for it in order.items
        ]
    else:
        raise HTTPException(status_code=400, detail="Order items are required")

    # Set the user_id from the authenticated user
    internal_order = OrderCreate(
        order_id=None,
        user_id=user.id,
        items=items_source,
        notes=order.notes,
    )
    # Opt-in merge only when explicitly requested; default is to create a fresh order
    merge_flag = (x_merge_with_latest or "").lower() in ("1", "true", "yes")
    new_order = await create_order(
        db,
        internal_order,
        idempotency_key=x_idempotency_key,
        merge_fallback=merge_flag,
    )
    # No cart coupling here; totals are computed from exact items provided

    
    # Clear cache after creating order
    from app.core.cache import invalidate_cache_pattern
    await invalidate_cache_pattern("orders:")
    
    created_compact = format_tashkent_compact(new_order.created_at)
    # IMPORTANT: Avoid accessing new_order.items directly (may trigger async lazy-load)
    # Fetch items explicitly to prevent MissingGreenlet
    items_result = await db.execute(
        select(OrderItem, Slipper)
        .join(Slipper, Slipper.id == OrderItem.slipper_id)
        .where(OrderItem.order_id == new_order.id)
        .order_by(OrderItem.id.asc())
    )
    items = items_result.all()
    return {
        "order_id": new_order.order_id,
        "status": new_order.status.value if hasattr(new_order.status, 'value') else str(new_order.status),
        "total_amount": new_order.total_amount,
        "notes": new_order.notes,
    # Compact local Tashkent time (YYYY-MM-DD HH:MM)
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

@router.post("/from-cart")
async def create_order_from_cart(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    clear_cart: bool = Query(False, description="If true, clears cart after creating the order"),
):
    """Create a new order from the current user's cart items.
    If `clear_cart=true` is provided, the user's cart will be emptied after order creation.
    """
    from app.crud.cart import get_cart, get_cart_totals, clear_cart as clear_cart_fn
    from app.schemas.order import OrderCreate, OrderItemCreate

    cart = await get_cart(db, user.id)
    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

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
    new_order = await create_order(
        db,
        internal_order,
        idempotency_key=None,
        merge_fallback=False,
    )

    # Align total to cart total for consistency
    _, _, cart_total = await get_cart_totals(db, user.id)
    if cart_total and abs((new_order.total_amount or 0.0) - cart_total) > 0.5:
        new_order.total_amount = float(cart_total)
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
        select(OrderItem, Slipper)
        .join(Slipper, Slipper.id == OrderItem.slipper_id)
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
    finance: str | None = Query(
        None,
        description="Financial filter: 'paid_refunded' shows only orders with latest payment PAID or REFUNDED",
    ),
):
    """List orders.
    - Regular user: only their own orders.
    - Admin: all users' orders.
    Pagination & status filtering removed per request.
    """
    try:
        # Build a per-user cache key shim by referencing args in cached decorator
        # The cached decorator includes function args in key, so ensure user.id and finance are present
        _ = user.id  # referenced for clarity; no-op
        _ = finance
        if finance and finance.lower() == "paid_refunded":
            statuses = [PaymentStatus.PAID, PaymentStatus.REFUNDED]
            if user.is_admin:
                logger.info("Admin %s listing orders with finance filter paid_refunded", user.name)
                rows, total = await get_orders_by_payment_statuses(db, statuses=statuses, load_relationships=False)
            else:
                logger.info("User %s listing OWN orders with finance filter paid_refunded", user.name)
                rows, total = await get_orders_by_payment_statuses(db, statuses=statuses, user_id=user.id, load_relationships=False)
            # rows are tuples (order, payment_status)
            # Batch load items for all orders
            orders_only = [order for order, _ in rows]
            order_ids = [o.id for o in orders_only]
            # Batch load users for these orders to avoid N+1
            user_ids = list({o.user_id for o in orders_only})
            users_by_id: dict[int, tuple[str, str]] = {}
            if user_ids:
                from app.models.user import User as _User
                user_rows = await db.execute(select(_User.id, _User.name, _User.surname).where(_User.id.in_(user_ids)))
                for uid, nm, sn in user_rows.all():
                    users_by_id[int(uid)] = (nm, sn)
            items_by_order: dict[int, list[dict]] = {}
            if order_ids:
                data = await db.execute(
                    select(OrderItem, Slipper)
                    .join(Slipper, Slipper.id == OrderItem.slipper_id)
                    .where(OrderItem.order_id.in_(order_ids))
                    .order_by(OrderItem.id.asc())
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

            result = []
            for order, pay_status in rows:
                full_name = None
                tup = users_by_id.get(order.user_id)
                if tup:
                    full_name = f"{tup[0]} {tup[1]}".strip()
                result.append({
                    "id": order.id,
                    "order_id": order.order_id,
                    "user_id": order.user_id,
                    "user_name": full_name,
                    "status": order.status.value,
                    "payment_status": (
                        "success" if pay_status == PaymentStatus.PAID else (
                            "refunded" if pay_status == PaymentStatus.REFUNDED else None
                        )
                    ),
                    "total_amount": order.total_amount,
                    "created_at": format_tashkent_compact(order.created_at),
                    "updated_at": format_tashkent_compact(order.updated_at),
                    "items": items_by_order.get(order.id, []),
                })
            return result
        else:
            if user.is_admin:
                logger.info(f"Admin {user.name} listing ALL orders")
                orders, total = await get_orders(db, skip=0, limit=100000, load_relationships=False)
            else:
                logger.info(f"User {user.name} listing OWN orders")
                orders, total = await get_orders(db, skip=0, limit=100000, user_id=user.id, load_relationships=False)
            # Batch load items for all orders
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
                    select(OrderItem, Slipper)
                    .join(Slipper, Slipper.id == OrderItem.slipper_id)
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
            select(OrderItem, Slipper)
            .join(Slipper, Slipper.id == OrderItem.slipper_id)
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
        select(OrderItem, Slipper)
        .join(Slipper, Slipper.id == OrderItem.slipper_id)
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