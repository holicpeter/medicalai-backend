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
