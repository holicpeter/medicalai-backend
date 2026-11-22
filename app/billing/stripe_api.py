"""The few Stripe API calls this app makes, over plain HTTPS.

Deliberately not the `stripe` SDK: we need four endpoints and webhook
signature checking, httpx is already a dependency, and every call stays
visible here. Docs: https://docs.stripe.com/api
"""
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

API_BASE = "https://api.stripe.com/v1"
# How old a webhook signature may be — Stripe's own libraries use 5 minutes.
SIGNATURE_TOLERANCE_SECONDS = 300


class StripeError(Exception):
    """Stripe answered with an error, or could not be reached."""


def _encode(params: Dict[str, Any], prefix: str = "") -> List[Tuple[str, str]]:
    """Stripe's form encoding: nested dicts/lists become a[b][0]=c."""
    out: List[Tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(_encode(value, name))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(_encode(item, f"{name}[{i}]"))
                else:
                    out.append((f"{name}[{i}]", _scalar(item)))
        else:
            out.append((name, _scalar(value)))
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _request(method: str, path: str, params: Optional[Dict[str, Any]] = None,
             idempotency_key: Optional[str] = None) -> Dict[str, Any]:
    if not settings.STRIPE_SECRET_KEY:
        raise StripeError("STRIPE_SECRET_KEY is not set")
    headers = {"Stripe-Version": "2024-06-20"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.request(
                method,
                f"{API_BASE}{path}",
                auth=(settings.STRIPE_SECRET_KEY, ""),
                data=_encode(params or {}) if method != "GET" else None,
                params=_encode(params or {}) if method == "GET" else None,
                headers=headers,
            )
    except httpx.HTTPError as e:
        raise StripeError(f"Stripe unreachable: {e}") from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message") or response.text[:200]
        raise StripeError(f"Stripe {response.status_code}: {message}")
    return body


def create_customer(email: str, user_id: int) -> Dict[str, Any]:
    return _request(
        "POST", "/customers",
        {"email": email, "metadata": {"user_id": user_id}},
        idempotency_key=f"customer-user-{user_id}",
    )


def create_checkout_session(*, customer_id: str, price_id: str, user_id: int, plan: str,
                            success_url: str, cancel_url: str,
                            trial_days: int = 0, automatic_tax: bool = False) -> Dict[str, Any]:
    subscription_data: Dict[str, Any] = {"metadata": {"user_id": user_id, "plan": plan}}
    if trial_days > 0:
        subscription_data["trial_period_days"] = trial_days
    params: Dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "client_reference_id": str(user_id),
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
        "subscription_data": subscription_data,
        "metadata": {"user_id": user_id, "plan": plan},
    }
    if automatic_tax:
        params["automatic_tax"] = {"enabled": True}
        params["customer_update"] = {"address": "auto", "name": "auto"}
        params["tax_id_collection"] = {"enabled": True}
    return _request("POST", "/checkout/sessions", params)


def create_portal_session(customer_id: str, return_url: str) -> Dict[str, Any]:
    return _request("POST", "/billing_portal/sessions",
                    {"customer": customer_id, "return_url": return_url})


def retrieve_subscription(subscription_id: str) -> Dict[str, Any]:
    return _request("GET", f"/subscriptions/{subscription_id}")


def cancel_subscription(subscription_id: str) -> Dict[str, Any]:
    """Cancel immediately (used when the account itself is deleted)."""
    return _request("DELETE", f"/subscriptions/{subscription_id}")


def verify_webhook(payload: bytes, signature_header: str, secret: str,
                   now: Optional[float] = None) -> Dict[str, Any]:
    """Check the Stripe-Signature header and return the parsed event.

    Scheme: header "t=<ts>,v1=<hex>[,v1=...]"; v1 is HMAC-SHA256 of
    "<ts>.<raw body>" with the endpoint's signing secret.
    https://docs.stripe.com/webhooks#verify-manually
    """
    if not secret:
        raise StripeError("STRIPE_WEBHOOK_SECRET is not set")
    timestamp = None
    signatures: List[str] = []
    for part in (signature_header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if not timestamp or not signatures:
        raise StripeError("Malformed Stripe-Signature header")
    try:
        ts = int(timestamp)
    except ValueError as e:
        raise StripeError("Malformed Stripe-Signature timestamp") from e
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_TOLERANCE_SECONDS:
        raise StripeError("Stripe signature timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise StripeError("Stripe signature does not match")
    try:
        return json.loads(payload)
    except ValueError as e:
        raise StripeError("Webhook body is not JSON") from e
