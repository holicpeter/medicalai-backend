"""Per-user wearable connections (Withings).

Every user connects their own Withings account: the OAuth tokens are stored
per user and encrypted, the callback only accepts a consent that this browser
session started, one user never sees another's Withings data, and deleting or
exporting an account covers the connection without ever exposing the tokens.
Withings itself is never called: the token exchange is replaced by a stub.
"""
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.database import User, WearableConnection, get_session
from app.integrations import connections, token_crypto
from app.integrations import withings_connector
from app.main import app

_PASSWORD = "wearable-password-1"


@pytest.fixture(autouse=True)
def withings_app(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])
    monkeypatch.setenv("WITHINGS_CLIENT_ID", "client-id")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WITHINGS_REDIRECT_URI", "https://app.example/api/integrations/withings/callback")
    withings_connector._connectors.clear()

    async def fake_request_token(self, payload):
        # What Withings would answer to a successful code exchange.
        self._access_token = f"access-{self.user_id}"
        self._refresh_token = f"refresh-{self.user_id}"
        self._expires_at = 9_999_999_999
        self._save_tokens(1000 + self.user_id)
        return True

    monkeypatch.setattr(withings_connector.WithingsConnector, "_request_token", fake_request_token)

    synced = []

    async def fake_initial_sync(user_id):
        synced.append(user_id)

    monkeypatch.setattr(connections, "initial_sync", fake_initial_sync)
    yield synced
    withings_connector._connectors.clear()


def _user(email=None):
    client = TestClient(app, follow_redirects=False)
    r = client.post("/api/auth/register", json={
        "email": email or f"wear-{uuid.uuid4().hex[:10]}@example.com",
        "password": _PASSWORD, "gdpr_consent": True,
    })
    assert r.status_code == 201
    return client, r.json()["id"]


def _state_from(client):
    r = client.post("/api/connections/withings/connect")
    assert r.status_code == 200
    url = urlparse(r.json()["authorize_url"])
    assert url.netloc == "account.withings.com"
    return parse_qs(url.query)["state"][0]


def _callback(client, state, code="the-code"):
    return client.get("/api/integrations/withings/callback", params={"code": code, "state": state})


def _withings(connections_list):
    return next(c for c in connections_list if c["id"] == "withings")


def test_a_user_connects_their_own_withings(withings_app):
    client, user_id = _user()
    assert _withings(client.get("/api/connections").json()["connections"])["connected"] is False

    r = _callback(client, _state_from(client))
    assert r.status_code == 303
    assert r.headers["location"].endswith("/connections?withings=connected")
    assert withings_app == [user_id]  # the first sync was started

    status = _withings(client.get("/api/connections").json()["connections"])
    assert status["connected"] is True
    assert status["connected_at"]


def test_tokens_are_stored_encrypted():
    client, user_id = _user()
    _callback(client, _state_from(client))

    session = get_session()
    try:
        row = session.query(WearableConnection).filter_by(user_id=user_id, provider="withings").one()
        assert row.refresh_token_enc and f"refresh-{user_id}" not in row.refresh_token_enc
        assert token_crypto.decrypt(row.refresh_token_enc) == f"refresh-{user_id}"
    finally:
        session.close()


def test_a_consent_link_from_someone_else_is_refused():
    attacker, attacker_id = _user()
    victim, victim_id = _user()

    # The attacker starts a connection and gets the victim to finish it.
    r = _callback(victim, _state_from(attacker))
    assert r.status_code == 303
    assert "reason=session" in r.headers["location"]

    session = get_session()
    try:
        assert session.query(WearableConnection).filter(
            WearableConnection.user_id.in_([attacker_id, victim_id])).count() == 0
    finally:
        session.close()


def test_a_forged_or_missing_state_is_refused():
    client, _ = _user()
    assert "reason=expired" in _callback(client, "not-a-real-state").headers["location"]
    assert "reason=expired" in _callback(client, "").headers["location"]


def test_withings_verifying_the_callback_url_gets_a_200():
    assert TestClient(app).get("/api/integrations/withings/callback").status_code == 200


def test_declining_at_withings_comes_back_with_a_message():
    client, _ = _user()
    r = client.get("/api/integrations/withings/callback", params={"error": "access_denied"})
    assert "reason=denied" in r.headers["location"]


def test_one_users_withings_data_is_never_served_to_another():
    owner, _ = _user()
    _callback(owner, _state_from(owner))
    other, _ = _user()

    for path in ("sleep", "activity", "measures", "ecg", "hrv"):
        assert other.get(f"/api/integrations/withings/{path}").status_code == 401
    assert other.post("/api/connections/withings/sync").status_code == 401


def test_disconnect_removes_the_tokens():
    client, user_id = _user()
    _callback(client, _state_from(client))
    assert client.delete("/api/connections/withings").status_code == 200

    assert _withings(client.get("/api/connections").json()["connections"])["connected"] is False
    session = get_session()
    try:
        assert session.query(WearableConnection).filter_by(user_id=user_id).count() == 0
    finally:
        session.close()


def test_the_demo_account_cannot_connect(monkeypatch):
    from scripts.seed_demo_account import seed

    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "DEMO_EMAILS", [email])
    seed(email, _PASSWORD)
    client = TestClient(app, follow_redirects=False)
    assert client.post("/api/auth/login", json={"email": email, "password": _PASSWORD}).status_code == 200

    assert client.get("/api/integrations/withings/auth").status_code == 403
    assert client.post("/api/connections/withings/connect").status_code == 403


def test_deleting_the_account_removes_the_connection():
    client, user_id = _user()
    _callback(client, _state_from(client))
    r = client.post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 200

    session = get_session()
    try:
        assert session.query(WearableConnection).filter_by(user_id=user_id).count() == 0
    finally:
        session.close()


def test_the_export_lists_the_connection_but_never_its_tokens():
    client, user_id = _user()
    _callback(client, _state_from(client))
    r = client.get("/api/auth/export")
    assert r.status_code == 200
    exported = r.json()

    assert [c["provider"] for c in exported["connections"]] == ["withings"]
    assert f"refresh-{user_id}" not in r.text and "refresh_token_enc" not in r.text


def test_the_old_app_wide_connection_moves_to_the_admin(monkeypatch):
    from app.database.models import _get_engine

    admin_client, admin_id = _user()
    admin_email = admin_client.get("/api/auth/me").json()["email"]
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [admin_email])

    engine = _get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS withings_tokens (id integer PRIMARY KEY, "
            "withings_user_id bigint, access_token text, refresh_token text, expires_at double precision)"
        ))
        conn.execute(text("DELETE FROM withings_tokens"))
        conn.execute(text(
            "INSERT INTO withings_tokens (id, withings_user_id, access_token, refresh_token, expires_at) "
            "VALUES (1, 4242, 'old-access', 'old-refresh', 123.0)"
        ))

    assert "moved to" in connections.migrate_legacy_withings_tokens()
    assert connections.migrate_legacy_withings_tokens() is None  # nothing left to move

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM withings_tokens")).scalar() == 0
    session = get_session()
    try:
        row = session.query(WearableConnection).filter_by(user_id=admin_id, provider="withings").one()
        assert token_crypto.decrypt(row.refresh_token_enc) == "old-refresh"
        assert row.external_user_id == "4242"
    finally:
        session.close()


def test_a_token_encrypted_under_another_key_reads_as_disconnected(monkeypatch):
    stored = token_crypto.encrypt("secret-token")
    monkeypatch.setattr(settings, "SECRET_KEY", "a-completely-different-secret-key")
    assert token_crypto.decrypt(stored) is None
