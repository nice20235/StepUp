from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.acquiring import AcquiringClient


router = APIRouter(prefix="/payment", tags=["Payment"])


@router.get("/init/{order_id}")
async def init_payment(order_id: int, amount: int) -> RedirectResponse:
    """Initialize payment for given order and redirect user to bank page.

    This endpoint:
    - accepts order_id as path parameter and amount (in tiyin) as query parameter
    - calls AcquiringClient.get_payment_link(order_id, amount)
    - redirects the client to the returned payment_url
    """
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    client = AcquiringClient()

    try:
        payment_url = await client.get_payment_link(order_id=order_id, amount=amount)
    except Exception as exc:  # pragma: no cover - network/error details handled generically
        # Hide internal details from client; log can be added if needed
        raise HTTPException(status_code=502, detail="Failed to initialize payment") from exc

    return RedirectResponse(url=payment_url, status_code=302)
