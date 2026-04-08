from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.crud import transaction as transaction_crud
from app.schemas.transaction import (
    JSONRPCRequest,
    JSONRPCErrorResponse,
    JSONRPCSuccessResponse,
    JSONRPCError,
    CheckPerformParams,
    CheckPerformResult,
    CreateTransactionParams,
    CreateTransactionResult,
    PerformTransactionParams,
    PerformTransactionResult,
    CancelTransactionParams,
    CancelTransactionResult,
    CheckTransactionParams,
    CheckTransactionResult,
    GetStatementParams,
    GetStatementResult,
    StatementTransaction,
)

router = APIRouter()


def now_ms() -> int:
    """Return current Unix time in milliseconds."""
    return int(time.time() * 1000)


# Business error codes (from documentation)
ERROR_INVALID_ACCOUNT = -31001
ERROR_TRANSACTION_NOT_FOUND = -31003
ERROR_CANNOT_CANCEL = -31007
ERROR_TRANSACTION_FINISHED = -31008
ERROR_INVALID_REQUEST = -31050  # generic validation error within allowed range


def rpc_error(id_: Any, code: int, message: str) -> JSONResponse:
    err = JSONRPCErrorResponse(id=id_, error=JSONRPCError(code=code, message=message))
    return JSONResponse(status_code=200, content=err.model_dump())


def rpc_result(id_: Any, payload: dict[str, Any]) -> JSONResponse:
    res = JSONRPCSuccessResponse(id=id_, result=payload)
    return JSONResponse(status_code=200, content=res.model_dump())


@router.post("/rpc")
async def rpc_entrypoint(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Single JSON-RPC 2.0 entrypoint for merchant methods.

    Exposed methods:
    - CheckPerformTransaction
    - CreateTransaction
    - PerformTransaction
    - CancelTransaction
    - CheckTransaction
    - GetStatement
    """

    raw = await request.json()
    try:
        rpc_req = JSONRPCRequest(**raw)
    except Exception:
        # Invalid JSON-RPC request; use generic validation error within allowed range
        return rpc_error(None, ERROR_INVALID_REQUEST, "Invalid request")

    method = rpc_req.method
    rpc_id = rpc_req.id

    if method == "CheckPerformTransaction":
        try:
            params = CheckPerformParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        # Basic validation: amount must be positive, account must contain phone or (user and order)
        if params.amount <= 0:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid amount")

        account = params.account
        if not (account.phone or (account.user and account.order)):
            return rpc_error(rpc_id, ERROR_INVALID_ACCOUNT, "Invalid account")

        result = CheckPerformResult().model_dump()
        return rpc_result(rpc_id, result)

    if method == "CreateTransaction":
        try:
            params = CreateTransactionParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        if params.amount <= 0:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid amount")

        # Idempotent behaviour: if transaction with given acquirer id already exists,
        # return existing data instead of creating a duplicate.
        existing = await transaction_crud.get_by_acquirer_id(db, params.id)
        if existing:
            result = CreateTransactionResult(
                create_time=existing.create_time,
                transaction=existing.transaction,
                state=existing.state,
            )
            return rpc_result(rpc_id, result.model_dump())

        create_time = now_ms()
        # Merchant transaction identifier is generated on our side
        merchant_tx_id = params.id  # simple mapping acquirer id -> merchant transaction

        tx = await transaction_crud.create_transaction(
            db,
            acquirer_id=params.id,
            merchant_transaction_id=merchant_tx_id,
            amount=params.amount,
            state=1,
            create_time=create_time,
            account_data=params.account.model_dump(),
        )
        await db.commit()

        result = CreateTransactionResult(
            create_time=tx.create_time,
            transaction=tx.transaction,
            state=tx.state,
        )
        return rpc_result(rpc_id, result.model_dump())

    if method == "PerformTransaction":
        try:
            params = PerformTransactionParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        tx = await transaction_crud.get_by_acquirer_id(db, params.id)
        if not tx:
            return rpc_error(rpc_id, ERROR_TRANSACTION_NOT_FOUND, "Transaction not found")

        # Idempotent: if already paid, return existing data
        if tx.state == 2:
            result = PerformTransactionResult(
                transaction=tx.transaction,
                perform_time=tx.perform_time,
            )
            return rpc_result(rpc_id, result.model_dump())

        # Cannot perform cancelled or invalid state transactions
        if tx.state == -2:
            return rpc_error(rpc_id, ERROR_TRANSACTION_FINISHED, "Transaction finished")

        perform_time = now_ms()
        tx = await transaction_crud.update_transaction_state(
            db,
            tx=tx,
            state=2,
            perform_time=perform_time,
        )
        await db.commit()

        result = PerformTransactionResult(
            transaction=tx.transaction,
            perform_time=tx.perform_time,
        )
        return rpc_result(rpc_id, result.model_dump())

    if method == "CancelTransaction":
        try:
            params = CancelTransactionParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        tx = await transaction_crud.get_by_acquirer_id(db, params.id)
        if not tx:
            return rpc_error(rpc_id, ERROR_TRANSACTION_NOT_FOUND, "Transaction not found")

        # Idempotent: if already cancelled, return existing data
        if tx.state == -2:
            result = CancelTransactionResult(
                transaction=tx.transaction,
                cancel_time=tx.cancel_time,
                state=tx.state,
            )
            return rpc_result(rpc_id, result.model_dump())

        # According to spec, cancellation is only allowed before service is provided.
        # Treat state=2 (paid) as non-cancellable.
        if tx.state == 2:
            return rpc_error(rpc_id, ERROR_CANNOT_CANCEL, "Cannot cancel transaction")

        cancel_time = now_ms()
        tx = await transaction_crud.update_transaction_state(
            db,
            tx=tx,
            state=-2,
            cancel_time=cancel_time,
            reason=params.reason,
        )
        await db.commit()

        result = CancelTransactionResult(
            transaction=tx.transaction,
            cancel_time=tx.cancel_time,
            state=tx.state,
        )
        return rpc_result(rpc_id, result.model_dump())

    if method == "CheckTransaction":
        try:
            params = CheckTransactionParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        tx = await transaction_crud.get_by_acquirer_id(db, params.id)
        if not tx:
            return rpc_error(rpc_id, ERROR_TRANSACTION_NOT_FOUND, "Transaction not found")

        result = CheckTransactionResult(
            create_time=tx.create_time,
            perform_time=tx.perform_time,
            cancel_time=tx.cancel_time,
            transaction=tx.transaction,
            state=tx.state,
            reason=tx.reason,
        )
        return rpc_result(rpc_id, result.model_dump())

    if method == "GetStatement":
        try:
            params = GetStatementParams(**rpc_req.params)
        except Exception:
            return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Invalid parameters")

        txs = await transaction_crud.get_statement(db, from_time=params.from_, to_time=params.to)
        items: list[StatementTransaction] = []
        for tx in txs:
            items.append(
                StatementTransaction(
                    id=tx.id,
                    time=tx.create_time,
                    amount=tx.amount,
                    account=tx.account_data,
                    create_time=tx.create_time,
                    perform_time=tx.perform_time,
                    cancel_time=tx.cancel_time,
                    transaction=tx.transaction,
                    state=tx.state,
                    reason=tx.reason,
                )
            )

        result = GetStatementResult(transactions=items)
        return rpc_result(rpc_id, result.model_dump())

    # Unknown method
    return rpc_error(rpc_id, ERROR_INVALID_REQUEST, "Method not found")
