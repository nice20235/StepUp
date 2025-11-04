import uuid
import hmac
import hashlib
import httpx
from typing import Any, Dict, Optional
from datetime import datetime
from pydantic import BaseModel
from app.core.config import settings

# ----- Pydantic response models (minimal) -----
class OctoPrepareResponse(BaseModel):
    success: bool
    shop_transaction_id: Optional[str] = None
    octo_payment_UUID: Optional[str] = None  # normalized primary field used by endpoint
    octo_pay_url: Optional[str] = None
    error: Optional[int] = None
    errMessage: Optional[str] = None
    raw: Dict[str, Any]

class OctoRefundResponse(BaseModel):
    success: bool
    errCode: Optional[int] = None
    errMessage: Optional[str] = None
    raw: Dict[str, Any]

# ----- Helper: HMAC signature (if OCTO requires; placeholder here) -----
def _make_signature(payload: Dict[str, Any], secret: str) -> str:
    # Depending on OCTO requirements; placeholder for future use
    msg = "|".join(f"{k}={payload[k]}" for k in sorted(payload.keys()))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

# ----- Public API -----
def _extract_payment_uuid(data: Dict[str, Any]) -> Optional[str]:
    """Extract payment UUID from heterogeneous OCTO responses.
    Accepts variants like: octo_payment_UUID, octo_payment_uuid, payment_uuid (any case), nested in data{}.
    """
    if not data:
        return None
    # Flatten top-level + data section
    candidates = []
    top = data
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    merged_sources = [top, inner]
    for source in merged_sources:
        for k, v in source.items():
            if not isinstance(v, str):
                continue
            kl = k.lower()
            if "payment" in kl and "uuid" in kl:
                candidates.append(v)
    # Return first non-empty UUID-like (simple length >= 8)
    for c in candidates:
        if c and len(c) >= 8:
            return c
    return None

async def createPayment(total_sum: int, description: str) -> OctoPrepareResponse:
    """
    Create payment via OCTO prepare_payment (one-stage, auto_capture).

    - currency: UZS
    - payment_methods: uzcard, humo, bank_card
    - Do NOT send user_data
    """
    if total_sum <= 0:
        return OctoPrepareResponse(success=False, errMessage="total_sum must be positive", raw={})

    # Validate required settings
    missing = []
    if not settings.OCTO_SHOP_ID:
        missing.append("OCTO_SHOP_ID")
    if not settings.OCTO_SECRET:
        missing.append("OCTO_SECRET")
    if not settings.OCTO_RETURN_URL:
        missing.append("OCTO_RETURN_URL")
    if not settings.OCTO_NOTIFY_URL:
        missing.append("OCTO_NOTIFY_URL")
    if missing:
        return OctoPrepareResponse(success=False, errMessage=f"Missing settings: {', '.join(missing)}", raw={})

    shop_transaction_id = str(uuid.uuid4())

    payload: Dict[str, Any] = {
        # Exact fields per docs
        "octo_shop_id": int(settings.OCTO_SHOP_ID) if str(settings.OCTO_SHOP_ID).isdigit() else settings.OCTO_SHOP_ID,
        "octo_secret": settings.OCTO_SECRET,
        "shop_transaction_id": shop_transaction_id,
        "auto_capture": bool(settings.OCTO_AUTO_CAPTURE),
        "init_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test": bool(settings.OCTO_TEST),
        # user_data omitted intentionally
        "total_sum": float(total_sum),
        "currency": settings.OCTO_CURRENCY,
        "description": description,
            # Intentionally omit payment_methods so OCTO shows all enabled methods for the merchant
        "return_url": settings.OCTO_RETURN_URL,
        "notify_url": settings.OCTO_NOTIFY_URL,
        "language": settings.OCTO_LANGUAGE,
        # ttl optional
    }

    # Merge optional provider-specific parameters from settings (if provided)
    if getattr(settings, "OCTO_EXTRA_PARAMS", None):
        try:
            # Shallow merge: top-level keys from OCTO_EXTRA_PARAMS override/add to payload
            extra = dict(settings.OCTO_EXTRA_PARAMS)
            payload.update(extra)
        except Exception:
            # Ignore malformed extra params to avoid breaking payment creation
            pass

    # According to spec, OCTO may expect secret in header Authorization or X-Auth; here we put Bearer OCTO_SECRET.
    headers = {"Content-Type": "application/json"}
    url = f"{settings.OCTO_API_BASE}/prepare_payment"

    async with httpx.AsyncClient(timeout=20, trust_env=True) as client:
        try:
            # Debug log URL to help diagnose DNS issues
            print(f"[OCTO] POST {url}")
            resp = await client.post(url, json=payload, headers=headers)
            data = resp.json()
        except Exception as e:
            return OctoPrepareResponse(success=False, errMessage=f"HTTP error: {e}", raw={})
    # Per docs, error==0 on success; data contains payment info
    if data.get("error") != 0:
        return OctoPrepareResponse(
            success=False,
            error=data.get("error"),
            errMessage=data.get("errMessage") or data.get("errorMessage") or "Unknown OCTO error",
            raw=data,
        )
    d = data.get("data") or {}
    payment_uuid = (
        data.get("octo_payment_UUID")
        or d.get("octo_payment_UUID")
        or data.get("octo_payment_uuid")
        or d.get("octo_payment_uuid")
        or data.get("payment_uuid")
        or d.get("payment_uuid")
        or _extract_payment_uuid(data)
    )
    return OctoPrepareResponse(
        success=True,
        shop_transaction_id=shop_transaction_id,
        octo_payment_UUID=payment_uuid,
        octo_pay_url=data.get("octo_pay_url") or d.get("octo_pay_url"),
        raw=data,
    )

async def refundPayment(octo_payment_UUID: str, amount: int) -> OctoRefundResponse:
    """
    Refund payment via OCTO API. Minimum amount is 1 USD (equivalent in UZS).
    If OCTO doesn't accept UZS for refund amount, adapt as necessary.
    """
    if not octo_payment_UUID:
        return OctoRefundResponse(success=False, errMessage="payment UUID required", raw={})
    if amount <= 0:
        return OctoRefundResponse(success=False, errMessage="amount must be positive", raw={})

    # Optionally enforce min refund 1 USD equivalence if rate is provided.
    # If the rate isn't set, we skip local enforcement and rely on OCTO to validate.
    if settings.OCTO_USD_UZS_RATE:
        try:
            min_uzs = int(round(1.0 * float(settings.OCTO_USD_UZS_RATE)))
            if amount < min_uzs:
                return OctoRefundResponse(success=False, errMessage=f"Minimum refund is >= {min_uzs} UZS (1 USD)", raw={})
        except Exception:
            # If the provided rate is invalid, ignore local check and rely on OCTO
            pass

    # Validate required settings
    missing = []
    if not settings.OCTO_SHOP_ID:
        missing.append("OCTO_SHOP_ID")
    if not settings.OCTO_SECRET:
        missing.append("OCTO_SECRET")
    if missing:
        return OctoRefundResponse(success=False, errMessage=f"Missing settings: {', '.join(missing)}", raw={})

    payload: Dict[str, Any] = {
        "octo_shop_id": int(settings.OCTO_SHOP_ID) if str(settings.OCTO_SHOP_ID).isdigit() else settings.OCTO_SHOP_ID,
        "shop_refund_id": str(uuid.uuid4()),
        "octo_secret": settings.OCTO_SECRET,
        "octo_payment_UUID": octo_payment_UUID,
        "amount": float(amount),
    }

    headers = {"Content-Type": "application/json"}
    url = f"{settings.OCTO_API_BASE}/refund"

    async with httpx.AsyncClient(timeout=20, trust_env=True) as client:
        try:
            print(f"[OCTO] POST {url}")
            resp = await client.post(url, json=payload, headers=headers)
            data = resp.json()
        except Exception as e:
            return OctoRefundResponse(success=False, errMessage=f"HTTP error: {e}", raw={})

    if data.get("error") != 0:
        return OctoRefundResponse(
            success=False,
            errMessage=data.get("errMessage") or data.get("errorMessage") or "Unknown OCTO error",
            raw=data,
        )

    return OctoRefundResponse(success=True, raw=data)
