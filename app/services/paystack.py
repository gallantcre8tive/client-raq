"""Paystack initialize + webhook signature verification."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.config import get_settings


def paystack_configured() -> bool:
    s = get_settings()
    return bool(getattr(s, "PAYSTACK_SECRET_KEY", None) and str(s.PAYSTACK_SECRET_KEY).strip())


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    """HMAC SHA512 of raw body with secret key must match x-paystack-signature."""
    secret = (getattr(get_settings(), "PAYSTACK_SECRET_KEY", None) or "").encode("utf-8")
    if not secret or not signature:
        return False
    computed = hmac.new(secret, body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature)


async def initialize_transaction(
    *,
    email: str,
    amount_kobo: int,
    reference: str,
    callback_url: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create a Paystack transaction.
    Returns {ok, authorization_url, reference, error}.
    """
    s = get_settings()
    secret = (getattr(s, "PAYSTACK_SECRET_KEY", None) or "").strip()
    if not secret:
        return {"ok": False, "error": "Paystack is not configured (PAYSTACK_SECRET_KEY)."}

    payload = {
        "email": email,
        "amount": int(amount_kobo),
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": metadata or {},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.paystack.co/transaction/initialize",
                json=payload,
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
            )
            data = r.json()
            if r.status_code >= 400 or not data.get("status"):
                return {
                    "ok": False,
                    "error": data.get("message") or f"Paystack error HTTP {r.status_code}",
                    "raw": data,
                }
            inner = data.get("data") or {}
            return {
                "ok": True,
                "authorization_url": inner.get("authorization_url"),
                "access_code": inner.get("access_code"),
                "reference": inner.get("reference") or reference,
            }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def verify_transaction(reference: str) -> dict[str, Any]:
    """Confirm transaction status with Paystack API."""
    s = get_settings()
    secret = (getattr(s, "PAYSTACK_SECRET_KEY", None) or "").strip()
    if not secret:
        return {"ok": False, "error": "Paystack not configured"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"https://api.paystack.co/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
            data = r.json()
            if not data.get("status"):
                return {"ok": False, "error": data.get("message") or "verify failed", "raw": data}
            inner = data.get("data") or {}
            return {
                "ok": True,
                "status": inner.get("status"),  # success
                "amount": inner.get("amount"),
                "currency": inner.get("currency"),
                "reference": inner.get("reference"),
                "customer_email": (inner.get("customer") or {}).get("email"),
                "metadata": inner.get("metadata") or {},
                "raw": data,
            }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def parse_webhook_event(body: bytes) -> dict[str, Any]:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}
