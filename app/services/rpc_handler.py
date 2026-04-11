from __future__ import annotations

import time
import logging
from typing import Any, Dict, Tuple, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import transaction as transaction_crud
from app.crud.order import update_order_status
from app.models.transaction import Transaction
from app.models.order import Order, OrderStatus


# Error codes from specification
ERROR_INVALID_ACCOUNT = -31001
ERROR_TRANSACTION_NOT_FOUND = -31003
ERROR_CANNOT_CANCEL = -31007
ERROR_TRANSACTION_FINISHED = -31008
ERROR_INVALID_REQUEST = -31050


def now_ms() -> int:
    """Return current Unix time in milliseconds."""
    return int(time.time() * 1000)


class RpcHandler:
    """Handler for JSON-RPC methods.

    Supported methods:
    - CheckPerformTransaction
    - CreateTransaction
    - PerformTransaction
    - CancelTransaction
    - CheckTransaction
    - GetStatement
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._logger = logging.getLogger(__name__)

    async def handle(self, method: str, params: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Route method to handler.

        Returns (result, error) where one of them is None.
        error has shape {"code": int, "message": str}.
        """

        if method == "CheckPerformTransaction":
            return await self._check_perform_transaction(params)
        if method == "CreateTransaction":
            return await self._create_transaction(params)
        if method == "PerformTransaction":
            return await self._perform_transaction(params)
        if method == "CancelTransaction":
            return await self._cancel_transaction(params)
        if method == "CheckTransaction":
            return await self._check_transaction(params)
        if method == "GetStatement":
            return await self._get_statement(params)

        return None, {"code": ERROR_INVALID_REQUEST, "message": "Method not found"}

    async def _check_perform_transaction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        amount = params.get("amount")
        account = params.get("account", {}) or {}

        if not isinstance(amount, int) or amount <= 0:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid amount"}

        # account must contain either phone or at least order id
        phone = account.get("phone")
        order = account.get("order")
        if not (phone or order):
            return None, {"code": ERROR_INVALID_ACCOUNT, "message": "Invalid account"}

        # If order is provided, validate that the amount matches order.total_amount
        # We accept public ids like "order_1" as well as raw numeric ids/strings.
        if order:
            order_db_id: Optional[int] = None

            # Public id style: "order_1"
            if isinstance(order, str) and order.startswith("order_"):
                _, _, maybe_id = order.partition("_")
                try:
                    order_db_id = int(maybe_id)
                except (TypeError, ValueError):
                    order_db_id = None
            # Plain numeric id (int or numeric string)
            elif isinstance(order, int):
                order_db_id = order
            elif isinstance(order, str) and order.isdigit():
                order_db_id = int(order)

            if order_db_id is not None:
                result = await self.db.execute(select(Order).where(Order.id == order_db_id))
                db_order = result.scalar_one_or_none()
                if not db_order:
                    return None, {"code": ERROR_INVALID_ACCOUNT, "message": "Order not found"}

                # Order totals in our system are stored as integer UZS amounts (possibly as float in DB),
                # while the acquiring protocol expects integer minor units. Compare as ints.
                order_total_int = int(db_order.total_amount or 0)
                if amount != order_total_int:
                    return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid amount"}

        return {"allow": True}, None

    async def _create_transaction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        acquirer_id = params.get("id")
        amount = params.get("amount")
        account = params.get("account", {}) or {}

        if not isinstance(acquirer_id, str) or not acquirer_id:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid id"}
        if not isinstance(amount, int) or amount <= 0:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid amount"}

        # Idempotent: if tx already exists, return existing state
        existing = await transaction_crud.get_by_acquirer_id(self.db, acquirer_id)
        if existing:
            return {
                "create_time": existing.create_time,
                "transaction": existing.transaction,
                "state": existing.state,
            }, None

        create_time = now_ms()
        merchant_tx_id = acquirer_id  # simple mapping 1:1

        tx = await transaction_crud.create_transaction(
            self.db,
            acquirer_id=acquirer_id,
            merchant_transaction_id=merchant_tx_id,
            amount=amount,
            state=1,
            create_time=create_time,
            account_data=account,
        )
        await self.db.commit()

        return {
            "create_time": tx.create_time,
            "transaction": tx.transaction,
            "state": tx.state,
        }, None

    async def _perform_transaction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        acquirer_id = params.get("id")
        if not isinstance(acquirer_id, str) or not acquirer_id:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid id"}

        tx = await transaction_crud.get_by_acquirer_id(self.db, acquirer_id)
        if not tx:
            return None, {"code": ERROR_TRANSACTION_NOT_FOUND, "message": "Transaction not found"}

        if tx.state == 2:
            # Already paid - idempotent
            return {
                "transaction": tx.transaction,
                "perform_time": tx.perform_time,
            }, None

        if tx.state == -2:
            return None, {"code": ERROR_TRANSACTION_FINISHED, "message": "Transaction finished"}

        perform_time = now_ms()
        tx = await transaction_crud.update_transaction_state(
            self.db,
            tx=tx,
            state=2,
            perform_time=perform_time,
        )

        # Try to mark the related order as PAID based on tx.account_data["order"]
        try:
            await self._mark_order_paid_from_transaction(tx)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.error("Failed to update order status from transaction %s: %s", tx.id, exc)

        await self.db.commit()

        return {
            "transaction": tx.transaction,
            "perform_time": tx.perform_time,
        }, None

    async def _cancel_transaction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        acquirer_id = params.get("id")
        reason = params.get("reason")

        if not isinstance(acquirer_id, str) or not acquirer_id:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid id"}

        tx = await transaction_crud.get_by_acquirer_id(self.db, acquirer_id)
        if not tx:
            return None, {"code": ERROR_TRANSACTION_NOT_FOUND, "message": "Transaction not found"}

        if tx.state == -2:
            # Already cancelled - idempotent
            return {
                "transaction": tx.transaction,
                "cancel_time": tx.cancel_time,
                "state": tx.state,
            }, None

        if tx.state == 2:
            # Service already provided, cannot cancel
            return None, {"code": ERROR_CANNOT_CANCEL, "message": "Cannot cancel transaction"}

        cancel_time = now_ms()
        tx = await transaction_crud.update_transaction_state(
            self.db,
            tx=tx,
            state=-2,
            cancel_time=cancel_time,
            reason=reason if isinstance(reason, int) else None,
        )
        await self.db.commit()

        return {
            "transaction": tx.transaction,
            "cancel_time": tx.cancel_time,
            "state": tx.state,
        }, None

    async def _check_transaction(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        acquirer_id = params.get("id")
        if not isinstance(acquirer_id, str) or not acquirer_id:
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid id"}

        tx = await transaction_crud.get_by_acquirer_id(self.db, acquirer_id)
        if not tx:
            return None, {"code": ERROR_TRANSACTION_NOT_FOUND, "message": "Transaction not found"}

        return {
            "create_time": tx.create_time,
            "perform_time": tx.perform_time,
            "cancel_time": tx.cancel_time,
            "transaction": tx.transaction,
            "state": tx.state,
            "reason": tx.reason,
        }, None

    async def _get_statement(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], None] | Tuple[None, Dict[str, Any]]:
        from_time = params.get("from")
        to_time = params.get("to")

        if not isinstance(from_time, int) or not isinstance(to_time, int):
            return None, {"code": ERROR_INVALID_REQUEST, "message": "Invalid period"}

        txs = await transaction_crud.get_statement(self.db, from_time=from_time, to_time=to_time)

        items: list[Dict[str, Any]] = []
        for tx in txs:
            items.append(
                {
                    "id": tx.id,
                    "time": tx.create_time,
                    "amount": tx.amount,
                    "account": tx.account_data,
                    "create_time": tx.create_time,
                    "perform_time": tx.perform_time,
                    "cancel_time": tx.cancel_time,
                    "transaction": tx.transaction,
                    "state": tx.state,
                    "reason": tx.reason,
                }
            )

        return {"transactions": items}, None

    async def _mark_order_paid_from_transaction(self, tx: Transaction) -> None:
        """Best-effort helper to mark an order as PAID when a transaction is performed.

        Uses the same semantics as CheckPerformTransaction: account.order may be
        a public id like "order_1" or a raw numeric id / numeric string.
        """

        account = tx.account_data or {}
        order = account.get("order")
        if not order:
            return

        order_db_id: Optional[int] = None

        if isinstance(order, str) and order.startswith("order_"):
            _, _, maybe_id = order.partition("_")
            try:
                order_db_id = int(maybe_id)
            except (TypeError, ValueError):
                order_db_id = None
        elif isinstance(order, int):
            order_db_id = order
        elif isinstance(order, str) and order.isdigit():
            order_db_id = int(order)

        if order_db_id is None:
            return

        # Update order status to PAID
        from app.crud.cart import clear_cart  # local import to avoid cycles

        updated_order = await update_order_status(self.db, order_id=order_db_id, status=OrderStatus.PAID)
        if not updated_order:
            return

        # After successful payment, clear the user's cart so they start fresh
        try:
            await clear_cart(self.db, updated_order.user_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning("Failed to clear cart after payment for order %s: %s", updated_order.id, exc)
