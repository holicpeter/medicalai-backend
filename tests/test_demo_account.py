"""The promo demo account (scripts/seed_demo_account.py).

Seeds it under a throwaway email, logs in the way a visitor would, and checks
that the screens have data to show, that re-running resets instead of
duplicating, and that visitors cannot delete the shared account.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from scripts.seed_demo_account import seed

_PASSWORD = "demo-password-123"


@pytest.fixture
def demo(monkeypatch):
    email = f"demo-{uuid.uuid4().hex[:10]}@example.com"
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "DEMO_EMAIL", email)
    monkeypatch.setattr(settings, "DEMO_EMAILS", [email])
    result = seed(email, _PASSWORD)
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"email": email, "password": _PASSWORD})
    assert r.status_code == 200
    return client, email, result


def test_the_demo_has_something_on_every_screen(demo):
    client, _, result = demo
    assert result["documents"] == 3 and result["family_members"] == 4

    summary = client.get("/api/analysis/summary").json()
    assert summary["has_data"] is True
    assert {"glucose", "ldl", "blood_pressure", "weight"} <= set(summary["latest_metrics"])

    assert client.get("/api/upload/history").json()["count"] == 3
    assert len(client.get("/api/manual/family").json()) == 4
    assert len(client.get("/api/chat/history").json()["messages"]) == 6
    assert client.get("/api/nutrition/entries").status_code == 200
    assert client.get("/api/apple-health/stats").status_code == 200


def test_reseeding_resets_instead_of_duplicating(demo):
    _, email, first = demo
    second = seed(email, _PASSWORD)
    assert second["health_records"] == first["health_records"]
    client = TestClient(app)
    client.post("/api/auth/login", json={"email": email, "password": _PASSWORD})
    assert len(client.get("/api/manual/family").json()) == 4


def test_visitors_cannot_delete_the_demo_account(demo):
    client, _, _ = demo
    r = client.post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


# ─── "Try the demo": no password, read-only ─────────────────────────────────

def test_demo_login_needs_no_password_and_says_it_is_the_demo(demo):
    _, email, _ = demo
    visitor = TestClient(app)
    r = visitor.post("/api/auth/demo-login")
    assert r.status_code == 200
    assert r.json()["email"] == email and r.json()["is_demo"] is True
    assert visitor.get("/api/analysis/summary").json()["has_data"] is True


def test_demo_login_is_503_when_the_demo_was_never_seeded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "DEMO_EMAIL", f"missing-{uuid.uuid4().hex[:8]}@example.com")
    assert TestClient(app).post("/api/auth/demo-login").status_code == 503


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/manual/health-record", {"record_date": "2026-01-15", "metric_type": "glucose", "value": "9.9"}),
    ("post", "/api/manual/family", {"first_name": "X", "last_name": "", "relationship_type": "mother", "gender": "female"}),
    ("post", "/api/chat/ask", {"question": "moje vlastné údaje…"}),
    ("post", "/api/nutrition/analyze-text", {"description": "ryža s kuracím"}),
    ("post", "/api/auth/delete-account", {"password": "x"}),
])
def test_the_demo_cannot_change_anything_or_call_ai(demo, method, path, body):
    visitor = TestClient(app)
    visitor.post("/api/auth/demo-login")
    r = getattr(visitor, method)(path, json=body)
    assert r.status_code == 403
    assert r.headers["X-Demo-Read-Only"] == "1"


def test_the_demo_cannot_start_a_claude_risk_analysis_with_a_get(demo):
    visitor = TestClient(app)
    visitor.post("/api/auth/demo-login")
    assert visitor.get("/api/predictions/risks", params={"use_claude": "true"}).status_code == 403
    assert visitor.get("/api/predictions/risks").status_code == 200


def test_the_demo_can_log_out_and_real_accounts_are_not_read_only(demo):
    visitor = TestClient(app)
    visitor.post("/api/auth/demo-login")
    assert visitor.post("/api/auth/logout").status_code == 200

    real = TestClient(app)
    r = real.post(
        "/api/auth/register",
        json={"email": f"real-{uuid.uuid4().hex[:8]}@example.com", "password": "real-password-1", "gdpr_consent": True},
    )
    assert r.json()["is_demo"] is False
    created = real.post(
        "/api/manual/health-record",
        json={"record_date": "2026-01-15", "metric_type": "glucose", "value": "5.1"},
    )
    assert created.status_code == 200
