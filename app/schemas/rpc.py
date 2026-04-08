from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ErrorObject(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: int | str | None


class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    result: Any
    id: int | str | None


class JsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    error: ErrorObject
    id: int | str | None
