"""Shared test setup.

The session cookie is Secure by default (AUTH_COOKIE_SECURE=true, see
app/config.py), and TestClient talks plain http://testserver, so a Secure
cookie would never be sent back and every logged-in request would be a 401.
Tests turn it off unless a test pins it itself.
"""
import pytest

from app.auth import dependencies as auth_dependencies
from app.config import settings


@pytest.fixture(autouse=True)
def insecure_cookie_for_testclient(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)


@pytest.fixture(autouse=True)
def reset_auth_rate_limit():
    # Every TestClient looks like the same IP to the register/login limiter.
    auth_dependencies._attempts.clear()
    yield
    auth_dependencies._attempts.clear()
