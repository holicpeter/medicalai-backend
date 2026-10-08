"""Saving meals: entered by hand, and on earlier days.

A meal can be saved without any AI analysis (the values typed in by the user),
and with logged_at it lands on the day it was eaten, so a forgotten meal can be
added afterwards. Neither spends AI credits.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_NUTRITION", 0)  # proves no credit is needed
    c = TestClient(app)
    r = c.post("/api/auth/register", json={
        "email": f"meal-{uuid.uuid4().hex[:8]}@example.com",
        "password": "meal-password-1", "gdpr_consent": True,
    })
    assert r.status_code == 201
    return c


def _meal(**extra):
    return {
        "items": [{"name": "ovsená kaša", "estimated_grams": 250, "calories": 320,
                   "protein_g": 11, "carbs_g": 52, "fat_g": 7, "confidence": 1.0}],
        "total_calories": 320, "total_protein_g": 11, "total_carbs_g": 52, "total_fat_g": 7,
        **extra,
    }


def test_a_manual_meal_is_saved_today_without_credits(client):
    assert client.post("/api/nutrition/entries", json=_meal()).status_code == 200
    today = client.get("/api/nutrition/entries").json()
    assert [e["items"][0]["name"] for e in today] == ["ovsená kaša"]


def test_a_meal_can_be_added_to_an_earlier_day(client):
    day = date.today() - timedelta(days=3)
    r = client.post("/api/nutrition/entries", json=_meal(logged_at=f"{day.isoformat()}T08:30:00"))
    assert r.status_code == 200
    assert r.json()["logged_at"].startswith(f"{day.isoformat()}T08:30")

    assert len(client.get("/api/nutrition/entries", params={"target_date": day.isoformat()}).json()) == 1
    assert client.get("/api/nutrition/entries").json() == []
    summary = client.get("/api/nutrition/summary", params={"target_date": day.isoformat()}).json()
    assert summary["totals"]["calories"] == 320


def test_future_and_very_old_dates_are_refused(client):
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    assert client.post("/api/nutrition/entries", json=_meal(logged_at=future)).status_code == 422
    assert client.post("/api/nutrition/entries", json=_meal(logged_at=old)).status_code == 422


def test_negative_values_are_refused(client):
    meal = _meal()
    meal["items"][0]["calories"] = -5
    assert client.post("/api/nutrition/entries", json=meal).status_code == 422
