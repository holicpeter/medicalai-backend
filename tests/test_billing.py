"""Stripe billing: signature check, webhook → subscription row, checkout and deletion.

No network: every Stripe API call is monkeypatched.
"""
import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.billing import service, stripe_api
from app.config import settings
from app.database import Subscription, get_session
from app.main import app

_PASSWORD = "billing-password-1"
_SECRET = "whsec_test_secret"


@pytest.fixture(autouse=True)
def billing_on(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO_MONTHLY", "price_pro_m")
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO_YEARLY", "price_pro_y")
    monkeypatch.setattr(settings, "STRIPE_PRICE_FAMILY_YEARLY", "")
    monkeypatch.setattr(settings, "STRIPE_PRICE_FOUNDING_YEARLY", "price_founding")


def _account():
    client = TestClient(app)
    r = client.post("/api/auth/register", json={
        "email": f"bill-{uuid.uuid4().hex[:12]}@example.com", "password": _PASSWORD, "gdpr_consent": True})
    assert r.status_code == 201
    return client, r.json()["id"]


def _signed(body: dict, secret: str = _SECRET, ts: int = None):
    payload = json.dumps(body).encode()
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={ts},v1={sig}"


def _subscription(sub_id, customer, user_id, status="active", price="price_pro_m"):
    return {
        "id": sub_id, "object": "subscription", "customer": customer, "status": status,
        "current_period_end": int(time.time()) + 30 * 86400, "cancel_at_period_end": False,
        "metadata": {"user_id": str(user_id)},
        "items": {"data": [{"price": {"id": price}}]},
    }


def test_signature_roundtrip_and_rejections():
    payload, header = _signed({"id": "evt_1"})
    assert stripe_api.verify_webhook(payload, header, _SECRET)["id"] == "evt_1"
    with pytest.raises(stripe_api.StripeError):
        stripe_api.verify_webhook(payload, header, "whsec_other")
    with pytest.raises(stripe_api.StripeError):
        stripe_api.verify_webhook(payload + b" ", header, _SECRET)
    old_payload, old_header = _signed({"id": "evt_1"}, ts=int(time.time()) - 3600)
    with pytest.raises(stripe_api.StripeError):
        stripe_api.verify_webhook(old_payload, old_header, _SECRET)


def test_form_encoding_nests_like_stripe():
    pairs = stripe_api._encode({"line_items": [{"price": "p", "quantity": 1}], "subscription_data": {"metadata": {"user_id": 5}}, "x": True})
    assert ("line_items[0][price]", "p") in pairs
    assert ("subscription_data[metadata][user_id]", "5") in pairs
    assert ("x", "true") in pairs


def test_new_user_is_free_and_plans_listed():
    client, _ = _account()
    assert client.get("/api/billing/status").json()["tier"] == "free"
    ids = [p["id"] for p in client.get("/api/billing/plans").json()["plans"]]
    assert ids == ["pro_monthly", "pro_yearly", "founding_yearly"]


def test_webhook_rejects_bad_signature():
    client = TestClient(app)
    payload, _ = _signed({"type": "customer.subscription.updated"})
    r = client.post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": "t=1,v1=00"})
    assert r.status_code == 400


def test_subscription_webhook_unlocks_pro_and_deletion_downgrades(monkeypatch):
    client, user_id = _account()
    event = {"id": "evt_a", "type": "customer.subscription.created",
             "data": {"object": _subscription("sub_a", "cus_a", user_id, status="trialing")}}
    payload, header = _signed(event)
    r = TestClient(app).post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": header})
    assert r.status_code == 200
    status = client.get("/api/billing/status").json()
    assert status["tier"] == "pro" and status["plan"] == "pro_monthly" and status["status"] == "trialing"

    event = {"id": "evt_b", "type": "customer.subscription.deleted",
             "data": {"object": _subscription("sub_a", "cus_a", user_id, status="canceled")}}
    payload, header = _signed(event)
    TestClient(app).post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": header})
    assert client.get("/api/billing/status").json()["tier"] == "free"


def test_checkout_completed_fetches_subscription(monkeypatch):
    client, user_id = _account()
    monkeypatch.setattr(stripe_api, "retrieve_subscription",
                        lambda sid: _subscription(sid, "cus_c", user_id, price="price_pro_y"))
    event = {"id": "evt_c", "type": "checkout.session.completed",
             "data": {"object": {"subscription": "sub_c", "customer": "cus_c", "client_reference_id": str(user_id)}}}
    payload, header = _signed(event)
    assert TestClient(app).post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": header}).status_code == 200
    assert client.get("/api/billing/status").json()["plan"] == "pro_yearly"


def test_checkout_creates_customer_once_and_returns_url(monkeypatch):
    client, user_id = _account()
    calls = {"customer": 0, "checkout": []}

    def fake_customer(email, uid):
        calls["customer"] += 1
        return {"id": "cus_new"}

    def fake_checkout(**kwargs):
        calls["checkout"].append(kwargs)
        return {"url": "https://checkout.stripe.com/c/pay/x"}

    monkeypatch.setattr(stripe_api, "create_customer", fake_customer)
    monkeypatch.setattr(stripe_api, "create_checkout_session", fake_checkout)
    for _ in range(2):
        r = client.post("/api/billing/checkout", json={"plan": "pro_monthly"})
        assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com")
    assert calls["customer"] == 1
    assert calls["checkout"][0]["price_id"] == "price_pro_m"
    assert calls["checkout"][0]["trial_days"] == settings.BILLING_TRIAL_DAYS
    assert client.post("/api/billing/checkout", json={"plan": "nope"}).status_code == 400


def test_checkout_off_without_key(monkeypatch):
    client, _ = _account()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 503


def test_account_deletion_cancels_subscription(monkeypatch):
    client, user_id = _account()
    service.apply_subscription(_subscription("sub_d", "cus_d", user_id))
    cancelled = []
    monkeypatch.setattr(stripe_api, "cancel_subscription", lambda sid: cancelled.append(sid) or {})
    r = client.post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 200
    assert cancelled == ["sub_d"]
    session = get_session()
    try:
        assert session.query(Subscription).filter_by(user_id=user_id).count() == 0
    finally:
        session.close()
