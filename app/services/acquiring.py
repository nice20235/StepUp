from __future__ import annotations

from typing import Any, Dict

import httpx

from app.core.config import settings


class AcquiringClient:
    """Client for calling acquirer REST methods.

    Methods strictly follow the specification:
    - POST api/info
    - POST api/debit
    - POST api/otp/confirm
    - GET api/info/{id}
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.ACQUIRING_BASE_URL.rstrip("/")

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/') }"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def _get(self, path: str) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/') }"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def info(
        self,
        *,
        clientId: str,
        invoiceId: str,
        amount: int,
        transactionType: str,
        currency: str,
    ) -> Dict[str, Any]:
        """Call POST api/info.

        Request:
        - clientId
        - invoiceId
        - amount (in tiyin)
        - transactionType
        - currency

        Response:
        - id
        - currency
        - amount
        - transactionType
        - properties
        """

        payload = {
            "clientId": clientId,
            "invoiceId": invoiceId,
            "amount": amount,
            "transactionType": transactionType,
            "currency": currency,
        }
        return await self._post("api/info", payload)

    async def debit(
        self,
        *,
        cardNumber: str,
        expiry: str,
        amount: int,
    ) -> Dict[str, Any]:
        """Call POST api/debit.

        Request:
        - cardNumber
        - expiry
        - amount (in tiyin)

        Response:
        - phone (masked)
        - otpId
        - codeLength
        """

        payload = {
            "cardNumber": cardNumber,
            "expiry": expiry,
            "amount": amount,
        }
        return await self._post("api/debit", payload)

    async def otp_confirm(self, *, otpId: str, code: str) -> Dict[str, Any]:
        """Call POST api/otp/confirm.

        Request:
        - otpId
        - code

        Response:
        - otpId
        """

        payload = {
            "otpId": otpId,
            "code": code,
        }
        return await self._post("api/otp/confirm", payload)

    async def get_info(self, *, id: str) -> Dict[str, Any]:
        """Call GET api/info/{id}.

        Response:
        - id
        - currency
        - amount
        - transactionType
        - client (imageName, name, imageContent)
        """

        path = f"api/info/{id}"
        return await self._get(path)

    async def get_payment_link(self, *, order_id: int, amount: int) -> str:
        """Temporary helper to obtain a payment URL for redirecting the user.

        NOTE: This is a stub implementation intended for development/testing only.
        Replace this method with a real call to the bank's API once their contract
        for creating payment links is finalized.

        For now we simply construct a demo URL based on the base_url so that the
        frontend flow can be wired and tested end-to-end.
        """

        # Example: https://bank.example.com/pay?order_id=123&amount=10000
        return f"{self.base_url}/pay?order_id={order_id}&amount={amount}"
