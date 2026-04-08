from __future__ import annotations

from fastapi import APIRouter, Depends

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.middleware.auth import verify_basic_auth
from app.schemas.rpc import JsonRpcRequest, JsonRpcResponse, JsonRpcError, ErrorObject
from app.services.rpc_handler import RpcHandler


router = APIRouter()


@router.post("/rpc", response_model=JsonRpcResponse | JsonRpcError)
async def rpc_endpoint(
    request: JsonRpcRequest,
    _: None = Depends(verify_basic_auth),
    db: AsyncSession = Depends(get_db),
) -> JsonRpcResponse | JsonRpcError:
    """Universal JSON-RPC 2.0 endpoint for acquiring calls.

    - Checks Basic Auth via dependency
    - Routes based on `method` field
    - Returns JSON-RPC 2.0 response or error
    """

    handler = RpcHandler(db)
    result, error = await handler.handle(request.method, request.params)

    if error is not None:
        return JsonRpcError(
            id=request.id,
            error=ErrorObject(code=error["code"], message=error["message"]),
        )

    return JsonRpcResponse(id=request.id, result=result)
