"""Registration, login and session handling for open, multi-user signup.

Every tester gets their own account with email + password (no invite code) and
exactly one Patient row created at registration — see app/api/auth.py. These
tests exercise the endpoints exactly as the frontend calls them: through
TestClient, cookies and all, not by calling the auth helpers directly, since
the whole point of this feature is what happens at the HTTP boundary (a wrong
password must not authenticate, a missing cookie must not authorize, and so
on).

The app falls back to a persistent SQLite file when DATABASE_URL is not set
(see app/database/models.py) — there is no per-test reset — so every test
here uses a fresh, randomly generated email rather than a fixed one, to stay
correct however many times the suite has already run against that file.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import dependencies as auth_dependencies
from app.config import settings
from app.main import app


def _email() -> str:
    return f"tester-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    # Same reasoning as test_proxy_secret.py: PROXY_SHARED_SECRET is unset by
    # default, but pin it explicitly so these tests do not depend on that.
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")


@pytest.fixture(autouse=True)
def reset_rate_limit():
    """The register/login limiter (app.auth.dependencies) is a module-level,
    in-memory bucket keyed by (client ip, bucket) — see check_rate_limit. Every
    TestClient instance in this process looks like the same IP, so without
    this the register-heavy tests below would eventually trip the same 429
    a real abusive script is meant to hit. Clearing it per test keeps each
    test's own limit accounting independent of how many other tests ran
    first, in this file or others."""
    auth_dependencies._attempts.clear()
    yield
    auth_dependencies._attempts.clear()


def _register(client, email=None, password="a-strong-enough-password", consent=True):
    return client.post(
        "/api/auth/register",
        json={"email": email or _email(), "password": password, "gdpr_consent": consent},
    )


def test_register_creates_a_user_and_its_own_patient(client):
    email = _email()
    r = _register(client, email=email)

    assert r.status_code == 201
    body = r.json()
    assert body["email"] == email
    assert isinstance(body["patient_id"], int)
    # The cookie is what authenticates the rest of the session — registration
    # logs the new account in immediately, no separate login step needed.
    assert settings.AUTH_COOKIE_NAME in client.cookies


def test_register_without_consent_is_rejected(client):
    r = _register(client, consent=False)
    assert r.status_code == 422


def test_register_rejects_a_short_password(client):
    r = _register(client, password="short1")
    assert r.status_code == 422


def test_duplicate_email_is_rejected(client):
    email = _email()
    first = _register(client, email=email)
    assert first.status_code == 201

    second_client = TestClient(app)
    second = _register(second_client, email=email)
    assert second.status_code == 409


def test_login_with_correct_password_succeeds(client):
    email = _email()
    password = "correct-horse-battery-staple"
    _register(client, email=email, password=password)

    fresh = TestClient(app)
    r = fresh.post("/api/auth/login", json={"email": email, "password": password})

    assert r.status_code == 200
    assert r.json()["email"] == email
    assert settings.AUTH_COOKIE_NAME in fresh.cookies


def test_login_with_wrong_password_is_rejected(client):
    email = _email()
    _register(client, email=email, password="the-real-password-123")

    fresh = TestClient(app)
    r = fresh.post("/api/auth/login", json={"email": email, "password": "not-the-right-one"})

    assert r.status_code == 401
    assert settings.AUTH_COOKIE_NAME not in fresh.cookies


def test_login_with_unknown_email_is_the_same_401_as_wrong_password(client):
    """Distinguishing the two would turn login into an account-existence oracle."""
    r = client.post(
        "/api/auth/login",
        json={"email": _email(), "password": "whatever-it-is"},
    )
    assert r.status_code == 401


def test_me_without_a_session_is_401(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_with_a_session_returns_the_logged_in_account(client):
    email = _email()
    _register(client, email=email)

    r = client.get("/api/auth/me")

    assert r.status_code == 200
    assert r.json()["email"] == email


def test_logout_clears_the_session(client):
    _register(client)
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    # TestClient keeps whatever cookie the Set-Cookie header on /logout leaves
    # behind, so the follow-up request is what actually proves the session is
    # gone rather than just inspecting the response headers.
    assert client.get("/api/auth/me").status_code == 401


def test_a_scoped_endpoint_requires_login_not_just_a_running_server(client):
    """Every data endpoint depends on get_current_patient_id — spot-check one."""
    r = client.get("/api/manual/family")
    assert r.status_code == 401
