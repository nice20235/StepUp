from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy import func, and_
from app.models.order import Order, OrderItem, OrderStatus
from app.models.stepup import StepUp
from app.schemas.order import OrderCreate, OrderUpdate, OrderItemCreate
from typing import Optional, List, Tuple
import logging
from app.models.payment import Payment, PaymentStatus
from datetime import datetime, timedelta
import uuid

logger = logging.getLogger(__name__)

async def get_order(db: AsyncSession, order_id: int, load_relationships: bool = True) -> Optional[Order]:
    """Get order by ID with optional relationship loading"""
    query = select(Order).where(Order.id == order_id)
    
    if load_relationships:
        query = query.options(
            joinedload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper)
        )
    
    result = await db.execute(query)
    return result.scalar_one_or_none()

async def get_orders(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = None,
    status: Optional[OrderStatus] = None,
    load_relationships: bool = True
) -> Tuple[List[Order], int]:
    """Get orders with pagination and filters - optimized"""
    # Build base query
    query = select(Order)
    conditions = []
    
    # Apply filters
    if user_id is not None:
        conditions.append(Order.user_id == user_id)
    if status is not None:
        conditions.append(Order.status == status)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Add relationships if needed
    if load_relationships:
        query = query.options(
            joinedload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper)
        )
    
    # Order by created_at for consistent results
    query = query.order_by(Order.created_at.desc())
    
    # Sequential execution to avoid SQLite concurrent operations
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    data_result = await db.execute(query.offset(skip).limit(limit))
    # Ensure uniqueness when eager loaders are involved
    orders = data_result.unique().scalars().all()
    
    return orders, total


async def get_orders_by_payment_statuses(
    db: AsyncSession,
    *,
    statuses: List[PaymentStatus],
    user_id: Optional[int] = None,
    load_relationships: bool = True,
) -> Tuple[List[Tuple[Order, Optional[PaymentStatus]]], int]:
    """Return orders where the latest payment status is in provided statuses.
    If user_id is provided, restrict to that user's orders.
    Returns list of tuples: (Order, latest_payment_status).
    """
    # Subquery to get latest payment per order by created_at
    latest_payment_sq = (
        select(
            Payment.order_id,
            func.max(Payment.created_at).label("max_created"),
        )
        .group_by(Payment.order_id)
        .subquery()
    )

    # Join orders with latest payments
    base = (
        select(Order, Payment.status)
        .join(latest_payment_sq, latest_payment_sq.c.order_id == Order.id, isouter=True)
        .join(
            Payment,
            (Payment.order_id == latest_payment_sq.c.order_id)
            & (Payment.created_at == latest_payment_sq.c.max_created),
            isouter=True,
        )
    )

    conditions = []
    if user_id is not None:
        conditions.append(Order.user_id == user_id)
    if statuses:
        conditions.append(Payment.status.in_(statuses))
    if conditions:
        base = base.where(and_(*conditions))

    base = base.order_by(Order.created_at.desc())
    # Guard against duplicate rows per order due to joins/ties in latest payments
    base = base.distinct(Order.id)

    # Count
    count_query = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Data
    if load_relationships:
        base = base.options(
            joinedload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper),
        )
    rows = (await db.execute(base)).unique().all()
    return rows, total

async def create_order(
    db: AsyncSession,
    order: OrderCreate,
    idempotency_key: str | None = None,
    *,
    merge_fallback: bool = False,
) -> Order:
    """Create new order with items"""
    # If idempotency_key is provided, return existing order to avoid duplicates
    if idempotency_key:
        # Scope idempotency to the same user to prevent cross-user leakage
        existing_q = select(Order).where(
            Order.idempotency_key == idempotency_key,
            Order.user_id == order.user_id,
        )
        existing = (await db.execute(existing_q)).scalar_one_or_none()
        if existing:
            # Load relationships and return
            result = await db.execute(
                select(Order)
                .options(
                    selectinload(Order.user),
                    selectinload(Order.items).selectinload(OrderItem.slipper)
                )
                .where(Order.id == existing.id)
            )
            return result.scalar_one()

    # Optional: merge into latest pending order within a recent window only if explicitly enabled.
    merge_target = None
    if merge_fallback:
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=5)
            merge_q = (
                select(Order)
                .where(
                    Order.user_id == order.user_id,
                    Order.status == OrderStatus.PENDING,
                    Order.payment_uuid.is_(None),
                    Order.created_at >= cutoff,
                )
                .order_by(Order.created_at.desc())
                .options(selectinload(Order.items))
            )
            merge_target = (await db.execute(merge_q)).scalars().first()
        except Exception:
            merge_target = None

    if merge_target is not None:
        # Build index of existing items by slipper_id
        existing_by_slipper = {it.slipper_id: it for it in (merge_target.items or [])}
        new_items_total = 0.0
        # For each incoming item, merge into existing or create new
        for item_data in order.items:
            slipper = (await db.execute(select(StepUp).where(StepUp.id == item_data.slipper_id))).scalar_one_or_none()
            if not slipper:
                raise ValueError(f"StepUp with ID {item_data.slipper_id} not found")
            # Enforce stock limit against existing quantity in merge target
            incoming_qty = int(item_data.quantity)
            from app.core.config import settings
            if incoming_qty > settings.ORDER_MAX_QTY_PER_ITEM:
                incoming_qty = settings.ORDER_MAX_QTY_PER_ITEM
            already = existing_by_slipper.get(item_data.slipper_id).quantity if item_data.slipper_id in existing_by_slipper else 0
            if already + incoming_qty > (slipper.quantity or 0):
                raise ValueError(
                    f"Requested quantity exceeds available stock for item {item_data.slipper_id} (requested={already + incoming_qty}, available={slipper.quantity})"
                )
            unit_price = slipper.price
            if item_data.slipper_id in existing_by_slipper:
                existing_item = existing_by_slipper[item_data.slipper_id]
                existing_item.quantity += incoming_qty
                existing_item.unit_price = unit_price  # keep current price snapshot
                existing_item.total_price = unit_price * existing_item.quantity
                db.add(existing_item)
            else:
                oi = OrderItem(
                    order_id=merge_target.id,
                    slipper_id=item_data.slipper_id,
                    quantity=incoming_qty,
                    unit_price=unit_price,
                    total_price=unit_price * incoming_qty,
                    notes=item_data.notes,
                )
                db.add(oi)
                new_items_total += oi.total_price
        # Recalculate total_amount (sum of all items)
        current_total = sum((it.total_price or 0.0) for it in (merge_target.items or []))
        merge_target.total_amount = current_total + new_items_total
        db.add(merge_target)
        await db.commit()
        await db.refresh(merge_target)
        # Second pass: re-load items to ensure totals reflect DB state, then recompute precise total
        reloaded = await db.execute(
            select(Order).options(selectinload(Order.items)).where(Order.id == merge_target.id)
        )
        merge_obj = reloaded.scalar_one()
        merge_obj.total_amount = sum((it.total_price or 0.0) for it in (merge_obj.items or []))
        db.add(merge_obj)
        await db.commit()
        await db.refresh(merge_obj)
        # Load relationships for response
        result = await db.execute(
            select(Order)
            .options(
                selectinload(Order.user),
                selectinload(Order.items).selectinload(OrderItem.slipper)
            )
            .where(Order.id == merge_target.id)
        )
        return result.scalar_one()
    # First pass: aggregate requested quantities and validate against stock
    from app.core.config import settings
    requested: dict[int, int] = {}
    item_ids: list[int] = []
    for it in order.items:
        q = int(it.quantity)
        if q > settings.ORDER_MAX_QTY_PER_ITEM:
            q = settings.ORDER_MAX_QTY_PER_ITEM
        requested[it.slipper_id] = requested.get(it.slipper_id, 0) + q
        if it.slipper_id not in item_ids:
            item_ids.append(it.slipper_id)

    if item_ids:
        rows = await db.execute(select(StepUp.id, StepUp.quantity, StepUp.price).where(StepUp.id.in_(item_ids)))
        step_by_id = {int(r[0]): (int(r[1] or 0), float(r[2] or 0.0)) for r in rows.all()}
        # Verify all exist and stock is sufficient
        for sid, qty in requested.items():
            if sid not in step_by_id:
                raise ValueError(f"StepUp with ID {sid} not found")
            available, _ = step_by_id[sid]
            if qty > available:
                raise ValueError(
                    f"Requested quantity exceeds available stock for item {sid} (requested={qty}, available={available})"
                )

    # Calculate total amount
    total_amount = 0.0
    order_items = []
    
    for item_data in order.items:
        # Get stepup to verify it exists and get current price
        slipper_result = await db.execute(select(StepUp).where(StepUp.id == item_data.slipper_id))
        slipper = slipper_result.scalar_one_or_none()
        if not slipper:
            raise ValueError(f"StepUp with ID {item_data.slipper_id} not found")
        # Clamp unrealistic quantities and rely on earlier stock validation
        qty = int(item_data.quantity)
        if qty > settings.ORDER_MAX_QTY_PER_ITEM:
            qty = settings.ORDER_MAX_QTY_PER_ITEM
        
        # Use current slipper price
        unit_price = slipper.price
        total_price = unit_price * qty
        total_amount += total_price
        
        # Create order item (use slipper_id)
        # Consolidate by slipper_id within this creation batch
        existing = next((oi for oi in order_items if oi.slipper_id == item_data.slipper_id), None)
        if existing:
            existing.quantity += qty
            existing.unit_price = unit_price
            existing.total_price = existing.unit_price * existing.quantity
            if item_data.notes and not existing.notes:
                existing.notes = item_data.notes
        else:
            order_item = OrderItem(
                slipper_id=item_data.slipper_id,
                quantity=qty,
                unit_price=unit_price,
                total_price=total_price,
                notes=item_data.notes
            )
            order_items.append(order_item)
    
    # Create order with temporary unique placeholder order_id if none provided
    provided_order_id = order.order_id
    temp_placeholder = None
    if not provided_order_id:
        # Use a unique temp value to satisfy unique constraint at INSERT time
        temp_placeholder = f"tmp-{uuid.uuid4().hex}"
    db_order = Order(
        order_id=provided_order_id if provided_order_id else temp_placeholder,
        user_id=order.user_id,
        total_amount=total_amount,
        notes=order.notes,
        status=OrderStatus.PENDING,
        idempotency_key=idempotency_key
    )
    db.add(db_order)
    await db.flush()  # obtain primary key

    # If no order_id provided, set sequential numeric based on primary key
    if not provided_order_id:
        db_order.order_id = str(db_order.id)
        db.add(db_order)

    # Attach items
    # Upsert items honoring unique (order_id, slipper_id)
    # Avoid lazy-loading relationship in async context (MissingGreenlet). Fetch explicitly.
    existing_items_result = await db.execute(
        select(OrderItem).where(OrderItem.order_id == db_order.id)
    )
    existing_items = existing_items_result.scalars().all()
    existing_items_db = {it.slipper_id: it for it in existing_items}
    for item in order_items:
        item.order_id = db_order.id
        if item.slipper_id in existing_items_db:
            db_item = existing_items_db[item.slipper_id]
            db_item.quantity += item.quantity
            db_item.unit_price = item.unit_price
            db_item.total_price = db_item.unit_price * db_item.quantity
            if item.notes and not db_item.notes:
                db_item.notes = item.notes
            db.add(db_item)
        else:
            db.add(item)

    await db.commit()
    await db.refresh(db_order)

    # Recompute total_amount from DB to ensure perfect accuracy
    recompute_result = await db.execute(
        select(func.coalesce(func.sum(OrderItem.total_price), 0.0)).where(
            OrderItem.order_id == db_order.id
        )
    )
    exact_total = float(recompute_result.scalar() or 0.0)
    if abs((db_order.total_amount or 0.0) - exact_total) > 1e-6:
        db_order.total_amount = exact_total
        db.add(db_order)
        await db.commit()
        await db.refresh(db_order)

    # Load relationships for response
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper)
        )
        .where(Order.id == db_order.id)
    )
    return result.scalar_one()

async def update_order(db: AsyncSession, db_order: Order, order_update: OrderUpdate) -> Order:
    """Update an existing order and return with relationships."""
    update_data = order_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_order, field, value)
    db.add(db_order)
    await db.commit()
    await db.refresh(db_order)
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper)
        )
        .where(Order.id == db_order.id)
    )
    return result.scalar_one()

async def update_order_status(db: AsyncSession, order_id: int, status: OrderStatus) -> Optional[Order]:
    """Update order status"""
    order = await get_order(db, order_id)
    if not order:
        return None
    
    order.status = status
    db.add(order)
    await db.commit()
    await db.refresh(order)
    
    # Load relationships
    result = await db.execute(
        select(Order)
        .options(
            selectinload(Order.user),
            selectinload(Order.items).selectinload(OrderItem.slipper)
        )
        .where(Order.id == order.id)
    )
    return result.scalar_one()

async def delete_order(db: AsyncSession, db_order: Order) -> bool:
    """Delete order (cascade will delete items)"""
    await db.delete(db_order)
    await db.commit()
    return True

async def update_order_payment_uuid(db: AsyncSession, order_id: int, payment_uuid: str) -> Optional[Order]:
    """Attach or update payment_uuid for an order (internal use only, not exposed)."""
    order = await get_order(db, order_id, load_relationships=False)
    if not order:
        return None
    order.payment_uuid = payment_uuid
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order

async def get_user_orders(
    db: AsyncSession,
    user_id: int,
    skip: int = 0,
    limit: int = 100
) -> Tuple[List[Order], int]:
    """Get orders for specific user"""
    return await get_orders(db, skip, limit, user_id=user_id) 