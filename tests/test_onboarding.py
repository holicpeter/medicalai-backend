"""The dashboard's "Get started" checklist reflects what the user has done."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    c = TestClient(app)
    r = c.post("/api/auth/register", json={
        "email": f"start-{uuid.uuid4().hex[:8]}@example.com",
        "password": "start-password-1", "gdpr_consent": True,
    })
    assert r.status_code == 201
    return c


def test_a_new_account_has_done_nothing_yet(client):
    assert client.get("/api/onboarding").json() == {"device": False, "results": False, "meals": False}


def test_a_meal_and_a_typed_in_result_tick_their_steps(client):
    meal = {"items": [{"name": "jablko", "calories": 95}], "total_calories": 95,
            "total_protein_g": 0, "total_carbs_g": 0, "total_fat_g": 0}
    assert client.post("/api/nutrition/entries", json=meal).status_code == 200
    r = client.post("/api/manual/health-record", json={
        "metric_type": "glucose", "value": "5.1", "unit": "mmol/L", "record_date": "2026-01-15",
    })
    assert r.status_code in (200, 201), r.text
    assert client.get("/api/onboarding").json() == {"device": False, "results": True, "meals": True}


def test_it_needs_a_login():
    assert TestClient(app).get("/api/onboarding").status_code == 401
