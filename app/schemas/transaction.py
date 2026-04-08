from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, RootModel


# ===== JSON-RPC 2.0 base types =====

class JSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: int | str | None


class JSONRPCError(BaseModel):
    code: int
    message: str


class JSONRPCErrorResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None
    error: JSONRPCError


class JSONRPCSuccessResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None
    result: dict[str, Any]


# ===== Merchant-side method parameter/response schemas =====

class Account(BaseModel):
    """Account object as described in the spec.

    Can contain either:
    - phone
    - or user + order
    """

    phone: Optional[str] = None
    user: Optional[str] = None
    order: Optional[str] = None


class CheckPerformParams(BaseModel):
    amount: int = Field(..., description="Amount in tiyin (1 UZS = 100 tiyin)")
    account: Account


class CheckPerformResult(BaseModel):
    allow: bool = True


class CreateTransactionParams(BaseModel):
    id: str = Field(..., description="Acquirer transaction identifier")
    time: int = Field(..., description="Unix time in milliseconds from acquirer")
    amount: int = Field(..., description="Amount in tiyin (1 UZS = 100 tiyin)")
    account: Account


class CreateTransactionResult(BaseModel):
    create_time: int
    transaction: str
    state: int


class PerformTransactionParams(BaseModel):
    id: str


class PerformTransactionResult(BaseModel):
    transaction: str
    perform_time: int


class CancelTransactionParams(BaseModel):
    id: str
    reason: int


class CancelTransactionResult(BaseModel):
    transaction: str
    cancel_time: int
    state: int


class CheckTransactionParams(BaseModel):
    id: str


class CheckTransactionResult(BaseModel):
    create_time: int
    perform_time: int
    cancel_time: int
    transaction: str
    state: int
    reason: Optional[int] = None


class GetStatementParams(BaseModel):
    from_: int = Field(..., alias="from", description="Start time (Unix ms)")
    to: int = Field(..., description="End time (Unix ms)")


class StatementTransaction(BaseModel):
    id: str
    time: int
    amount: int
    account: Account
    create_time: int
    perform_time: int
    cancel_time: int
    transaction: str
    state: int
    reason: Optional[int] = None


class GetStatementResult(BaseModel):
    transactions: list[StatementTransaction]


# ===== Public Transaction schema (internal use) =====

class TransactionOut(BaseModel):
    id: str
    transaction: str
    amount: int
    state: int
    create_time: int
    perform_time: int
    cancel_time: int
    account_data: dict[str, Any]
    reason: Optional[int] = None

    class Config:
        from_attributes = True
