"""Free daily AI allowance (app/auth/quota.py).

Open registration means every account can spend this app's Anthropic credit,
so each AI-backed endpoint is capped per user per day. These tests drive the
real endpoints with the model call stubbed out: what matters is the HTTP
behaviour a client sees — the call goes through while there is allowance,
then a 429 with the account message, and a failed AI call costs nothing.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import nutrition
from app.auth import quota
from app.config import settings
from app.main import app

_MEAL = {
    "items": [],
    "total_calories": 500.0,
    "total_protein_g": 20.0,
    "total_carbs_g": 60.0,
    "total_fat_g": 15.0,
    "overall_confidence": 0.7,
    "recommendation": "ok",
}


def _email() -> str:
    return f"quota-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_NUTRITION", 2)


@pytest.fixture
def stub_ai(monkeypatch):
    calls = []

    def fake(description):
        calls.append(description)
        return dict(_MEAL)

    monkeypatch.setattr(nutrition.analyzer, "analyze_meal_text", fake)
    return calls


def _client(email=None):
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"email": email or _email(), "password": "quota-password-123", "gdpr_consent": True},
    )
    assert r.status_code == 201
    return client


def _analyze(client):
    # A unique description every time, so the shared text cache never answers.
    return client.post(
        "/api/nutrition/analyze-text",
        json={"description": f"ryza s kuracim {uuid.uuid4().hex}"},
    )


def test_calls_go_through_until_the_limit_then_429_with_the_account_message(stub_ai):
    client = _client()

    assert _analyze(client).status_code == 200
    assert _analyze(client).status_code == 200

    r = _analyze(client)
    assert r.status_code == 429
    assert "Minuli ste dnešné bezplatné kredity" in r.json()["detail"]
    assert r.headers[quota.QUOTA_HEADER] == "nutrition"
    assert len(stub_ai) == 2, "the model must not be called once the allowance is used up"


def test_usage_endpoint_reports_what_is_left(stub_ai):
    client = _client()
    _analyze(client)

    usage = client.get("/api/auth/usage").json()
    item = next(i for i in usage["items"] if i["kind"] == "nutrition")
    assert item == {**item, "used": 1, "limit": 2, "remaining": 1, "exhausted": False}
    assert usage["any_exhausted"] is False

    _analyze(client)
    usage = client.get("/api/auth/usage").json()
    assert next(i for i in usage["items"] if i["kind"] == "nutrition")["exhausted"] is True
    assert usage["any_exhausted"] is True


def test_the_allowance_is_per_user(stub_ai):
    first = _client()
    _analyze(first)
    _analyze(first)
    assert _analyze(first).status_code == 429

    assert _analyze(_client()).status_code == 200


def test_a_failed_ai_call_is_refunded(monkeypatch):
    def broken(description):
        raise ValueError("model returned garbage")

    monkeypatch.setattr(nutrition.analyzer, "analyze_meal_text", broken)
    client = _client()

    for _ in range(3):
        assert _analyze(client).status_code == 502

    usage = client.get("/api/auth/usage").json()
    assert next(i for i in usage["items"] if i["kind"] == "nutrition")["used"] == 0


def test_admins_are_not_limited(monkeypatch, stub_ai):
    email = _email()
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [email])
    client = _client(email)

    for _ in range(4):
        assert _analyze(client).status_code == 200

    usage = client.get("/api/auth/usage").json()
    assert usage["unlimited"] is True


def test_usage_needs_a_login():
    assert TestClient(app).get("/api/auth/usage").status_code == 401


def _used(client, kind):
    return next(i for i in client.get("/api/auth/usage").json()["items"] if i["kind"] == kind)["used"]


def test_chat_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_CHAT", 0)
    r = _client().post("/api/chat/ask", json={"question": "ako sa mám?"})
    assert r.status_code == 429
    assert r.headers[quota.QUOTA_HEADER] == "chat"


def test_a_failed_chat_answer_is_refunded(monkeypatch):
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    client = _client()
    assert client.post("/api/chat/ask", json={"question": "ako sa mám?"}).status_code == 500
    assert _used(client, "chat") == 0


def test_document_upload_is_capped_but_csv_is_free(monkeypatch):
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_DOCUMENTS", 0)
    client = _client()

    r = client.post(
        "/api/upload/documents",
        files={"files": ("sprava.pdf", b"%PDF-1.4 not really", "application/pdf")},
    )
    assert r.status_code == 429
    assert r.headers[quota.QUOTA_HEADER] == "documents"

    csv = client.post(
        "/api/upload/documents",
        files={"files": ("data.csv", b"date,metric,value,unit\n", "text/csv")},
    )
    assert csv.status_code != 429
