#!/usr/bin/env python3
"""
Reconciliation script: find transactions with state=2 (paid)
and ensure corresponding orders are marked as PAID.

Run manually or schedule (cron / systemd timer) to keep DB consistent.
"""
import asyncio
import logging

from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.crud.order import update_order, update_order_status, get_order

logger = logging.getLogger("reconcile_payments")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def resolve_order_db_id(db, account_obj, tx):
    """Try to resolve an order DB id from account_data or transaction fields.

    Returns int or None.
    """
    # account_obj expected to be a dict or None
    if not account_obj:
        return None

    order = account_obj.get("order") or account_obj.get("order_id")
    if order is None:
        return None

    # If provided as 'order_123'
    if isinstance(order, str) and order.startswith("order_"):
        try:
            _, _, maybe_id = order.partition("_")
            return int(maybe_id)
        except Exception:
            pass

    # If numeric string or int
    if isinstance(order, int):
        return int(order)
    if isinstance(order, str) and order.isdigit():
        return int(order)

    # Fallback: try to find order by order_id string
    try:
        from app.models.order import Order as _Order
        res = await db.execute(select(_Order).where(_Order.order_id == str(order)).limit(1))
        found = res.scalar_one_or_none()
        if found:
            return int(found.id)
    except Exception as exc:
        logger.warning("Fallback DB lookup failed for tx=%s order=%s: %s", getattr(tx, 'id', None), order, exc)

    return None


async def reconcile_once():
    """Single reconciliation pass."""
    total_checked = 0
    total_marked = 0
    # Import models at runtime in a stable order so SQLAlchemy class registry
    # knows all model names before mappers are configured. Importing at
    # module import time caused 'Cart' unresolved mapper errors in some
    # environments due to circular imports.
    from app.models.transaction import Transaction
    from app.models.order import OrderStatus
    # Also import related model modules to ensure registry completeness
    from app.models.user import User  # noqa: F401
    from app.models.stepup import StepUp, Category, StepUpImage  # noqa: F401
    from app.models.order import Order, OrderItem  # noqa: F401
    from app.models.cart import Cart, CartItem  # noqa: F401

    async with AsyncSessionLocal() as db:
        # Select all performed (state=2) transactions
        result = await db.execute(select(Transaction).where(Transaction.state == 2))
        txs = result.scalars().all()

        for tx in txs:
            total_checked += 1
            try:
                acct = tx.account_data or {}
                order_db_id = await resolve_order_db_id(db, acct, tx)

                # Also consider transaction.transaction field as possible order_id or id
                if order_db_id is None and getattr(tx, 'transaction', None):
                    tr = str(tx.transaction)
                    # try numeric
                    if tr.isdigit():
                        order_db_id = int(tr)
                    else:
                        # try matching public order id
                        from app.models.order import Order as _Order
                        r = await db.execute(select(_Order).where(_Order.order_id == tr).limit(1))
                        f = r.scalar_one_or_none()
                        if f:
                            order_db_id = int(f.id)

                if order_db_id is None:
                    # Special-case: account_data.order might be a public cart identifier like 'cart_1'
                    order_field = acct.get('order') or acct.get('order_id')
                    if isinstance(order_field, str) and order_field.startswith('cart_'):
                        try:
                            _, _, maybe_cart_id = order_field.partition('_')
                            cart_id = int(maybe_cart_id)
                        except Exception:
                            cart_id = None

                        if cart_id is not None:
                            logger.info("tx %s references cart_%s; attempting to create/resolve order from cart", tx.id, cart_id)
                            # Load cart and its items
                            try:
                                from app.crud.cart import get_cart, get_cart_totals, clear_cart as _clear_cart
                                cart = await get_cart(db, int(acct.get('user') or 0)) if acct.get('user') else None
                                # Fallback: try loading by cart id directly
                                if not cart:
                                    # attempt by cart id and explicitly load items to avoid lazy-loads
                                    from app.models.cart import Cart as _Cart, CartItem as _CartItem
                                    res_cart = await db.execute(select(_Cart).where(_Cart.id == cart_id).limit(1))
                                    cart = res_cart.scalar_one_or_none()
                                    if cart:
                                        # explicitly load items for this cart
                                        items_res = await db.execute(select(_CartItem).where(_CartItem.cart_id == cart.id))
                                        items = items_res.scalars().all()
                                        # Build a simple plain object to avoid lazy-loading mapped attributes
                                        from types import SimpleNamespace
                                        cart = SimpleNamespace(id=cart.id, user_id=cart.user_id, items=items)

                                # At this point cart may have 'items' attribute attached
                                if not cart or not getattr(cart, 'items', None):
                                    logger.info("cart_%s not found or empty for tx %s", cart_id, tx.id)
                                    continue

                                # Compute cart total in UZS (matches frontend logic)
                                _, _, cart_total_uzs = await get_cart_totals(db, cart.user_id)
                                # Transaction amount is stored in tiyin (int)
                                tx_amount_uzs = int(tx.amount or 0) // 100

                                # Require amounts to match to avoid accidental mismatches
                                if int(round(float(cart_total_uzs))) != tx_amount_uzs:
                                    logger.warning("tx %s amount (%s tiyin -> %s UZS) does not match cart_%s total (%s UZS); skipping", tx.id, tx.amount, tx_amount_uzs, cart_id, cart_total_uzs)
                                    continue

                                # Check for an existing PENDING order for this cart/user that matches totals
                                from app.crud.order import get_user_orders as _get_user_orders, get_orders as _get_orders, update_order_status as _update_order_status, create_order as _create_order
                                # Try to find a recent PENDING order for same user and same total
                                user_orders, _ = await _get_user_orders(db, cart.user_id)
                                matched_order = None
                                for o in user_orders:
                                    try:
                                        # o.total_amount stored in tiyin
                                        if int(o.total_amount or 0) == int(round(float(cart_total_uzs)) * 100) and o.status.name == 'PENDING':
                                            matched_order = o
                                            break
                                    except Exception:
                                        continue

                                if matched_order is None:
                                    # Create an order from the cart (idempotent via order.create logic)
                                    from app.schemas.order import OrderCreate, OrderItemCreate
                                    items_source = [
                                        OrderItemCreate(slipper_id=ci.slipper_id, quantity=ci.quantity, unit_price=1.0, notes=None)
                                        for ci in cart.items
                                    ]
                                    internal_order = OrderCreate(order_id=None, user_id=cart.user_id, items=items_source, notes="Created from reconciliation")
                                    try:
                                        created = await _create_order(db, internal_order, idempotency_key=None)
                                        matched_order = created
                                        logger.info("Created order %s from cart_%s for tx %s", matched_order.id, cart_id, tx.id)
                                    except Exception as exc:
                                        logger.exception("Failed to create order from cart_%s for tx %s: %s", cart_id, tx.id, exc)
                                        continue

                                # At this point we have matched_order -> mark as PAID
                                updated = await _update_order_status(db, order_id=int(matched_order.id), status=OrderStatus.PAID)
                                if updated:
                                    total_marked += 1
                                    logger.info("Marked order %s as PAID (tx=%s) (from cart_%s)", matched_order.id, tx.id, cart_id)
                                    # Clear cart
                                    try:
                                        await _clear_cart(db, cart.user_id)
                                        logger.info("Cleared cart_%s for user %s after marking order %s as PAID", cart_id, cart.user_id, matched_order.id)
                                    except Exception as exc:
                                        logger.warning("Failed to clear cart_%s after marking order %s: %s", cart_id, matched_order.id, exc)
                                else:
                                    logger.warning("Failed to mark order %s as PAID (tx=%s) (from cart_%s)", matched_order.id, tx.id, cart_id)
                                continue
                            except Exception as exc:
                                logger.exception("Error handling cart_%s referenced by tx %s: %s", cart_id, tx.id, exc)
                                continue

                    logger.info("tx %s has no matching order (account_data=%s)", tx.id, acct)
                    continue

                # Load order and mark if needed
                from app.crud.order import get_order as _get_order
                order_obj = await _get_order(db, order_db_id, load_relationships=False)
                if not order_obj:
                    logger.info("tx %s -> resolved order id %s, but order not found in DB", tx.id, order_db_id)
                    continue

                if getattr(order_obj, 'status', None) == OrderStatus.PAID:
                    logger.debug("order %s already PAID (tx=%s)", order_db_id, tx.id)
                    continue

                # Mark PAID using existing CRUD function to ensure consistency
                updated = await update_order_status(db, order_id=order_db_id, status=OrderStatus.PAID)
                if updated:
                    total_marked += 1
                    logger.info("Marked order %s as PAID (tx=%s)", order_db_id, tx.id)
                    # Clear the user's cart after successful payment to match runtime behavior
                    try:
                        from app.crud.cart import clear_cart as _clear_cart
                        # updated is an Order object; clear_cart expects the user_id
                        await _clear_cart(db, updated.user_id)
                        logger.info("Cleared cart for user %s after marking order %s as PAID", updated.user_id, updated.id)
                    except Exception as exc:
                        logger.warning("Failed to clear cart for user %s after marking order %s: %s", getattr(updated, 'user_id', None), updated.id, exc)
                else:
                    logger.warning("Failed to mark order %s as PAID (tx=%s)", order_db_id, tx.id)

            except Exception as exc:
                logger.exception("Error reconciling tx %s: %s", getattr(tx, 'id', None), exc)

    logger.info("Reconciliation complete: checked=%d marked=%d", total_checked, total_marked)


def main():
    asyncio.run(reconcile_once())


if __name__ == '__main__':
    main()
