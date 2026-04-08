from __future__ import annotations

from typing import Any, Dict

import httpx

from app.core.config import settings


class EkayringClient:
    """HTTP client for calling external ekayring acquiring REST API.

    Base URL is taken from EKAYRING_BASE_URL in settings.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.EKAYRING_BASE_URL).rstrip("/")

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

    async def payment_check(
        self,
        *,
        client_id: str,
        invoice_id: str,
        amount: int,
        transaction_type: str,
        currency: str,
    ) -> Dict[str, Any]:
        """Call POST api/info on ekayring.

        Request:
        - clientId
        - invoiceId
        - amount (in tiyin)
        - transactionType
        - currency
        """

        payload = {
            "clientId": client_id,
            "invoiceId": invoice_id,
            "amount": amount,
            "transactionType": transaction_type,
            "currency": currency,
        }
        return await self._post("api/info", payload)

    async def payment_debit(
        self,
        *,
        card_number: str,
        expiry: str,
        amount: int,
    ) -> Dict[str, Any]:
        """Call POST api/debit on ekayring.

        Request:
        - cardNumber
        - expiry
        - amount (in tiyin)
        """

        payload = {
            "cardNumber": card_number,
            "expiry": expiry,
            "amount": amount,
        }
        return await self._post("api/debit", payload)

    async def confirm_otp(self, *, otp_id: str, code: str) -> Dict[str, Any]:
        """Call POST api/otp/confirm on ekayring.

        Request:
        - otpId
        - code
        """

        payload = {
            "otpId": otp_id,
            "code": code,
        }
        return await self._post("api/otp/confirm", payload)

    async def get_payment_info(self, *, payment_id: str) -> Dict[str, Any]:
        """Call GET api/info/{id} on ekayring."""

        path = f"api/info/{payment_id}"
        return await self._get(path)
