"""The SK | EN switch: server messages and AI instructions follow Accept-Language.

The web app sends the language the user picked with every request. Slovak
stays the default for clients that send nothing (the mobile app).
"""
import uuid

from fastapi.testclient import TestClient

from app.auth import quota
from app.config import settings
from app.i18n import lang_from_header
from app.main import app


def _client(lang=None):
    headers = {"Accept-Language": lang} if lang else {}
    return TestClient(app, headers=headers)


def test_header_parsing():
    assert lang_from_header("en") == "en"
    assert lang_from_header("en-GB,en;q=0.9") == "en"
    assert lang_from_header("sk") == "sk"
    assert lang_from_header(None) == "sk"
    assert lang_from_header("de-DE") == "sk"


def test_login_errors_follow_the_language(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    body = {"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com", "password": "wrong-password-1"}
    assert _client("en").post("/api/auth/login", json=body).json()["detail"] == "Wrong email or password."
    assert _client("sk").post("/api/auth/login", json=body).json()["detail"] == "Nesprávny email alebo heslo."
    assert _client().post("/api/auth/login", json=body).json()["detail"] == "Nesprávny email alebo heslo."


def test_validation_messages_follow_the_language(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    body = {"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "password": "long-enough-1", "gdpr_consent": False}
    detail = _client("en").post("/api/auth/register", json=body).json()["detail"]
    assert "Consent to the processing of health data" in str(detail)


def test_quota_message_and_labels_follow_the_language(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_CHAT", 0)
    client = _client("en")
    r = client.post("/api/auth/register", json={
        "email": f"q-{uuid.uuid4().hex[:6]}@example.com", "password": "long-enough-1", "gdpr_consent": True,
    })
    assert r.status_code == 201
    r = client.post("/api/chat/ask", json={"question": "hello?"})
    assert r.status_code == 429
    assert r.headers[quota.QUOTA_HEADER] == "chat"
    assert r.json()["detail"].startswith("You've used today's free credits for AI chat messages")
    labels = {i["kind"]: i["label"] for i in client.get("/api/auth/usage").json()["items"]}
    assert labels["chat"] == "AI chat messages"


def test_the_chat_tells_the_ai_to_answer_in_english(monkeypatch):
    from app.api import chat
    from app.i18n import _lang

    token = _lang.set("en")
    try:
        assert "Answer ONLY in English" in chat._language_rule()
    finally:
        _lang.reset(token)
    assert "slovenskom" in chat._language_rule()
