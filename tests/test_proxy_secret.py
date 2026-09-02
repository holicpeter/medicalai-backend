"""Only the Cloudflare Worker should be able to reach this API.

Cloudflare Access guards medicalai.peterholic.com, but the Railway hostname is
public and a request sent straight to it never passes through Access. The Worker
attaches a shared secret to everything it forwards; these tests pin the check
that turns that into the only way in.

The off-by-default behaviour is as important as the check itself — the code has
to be deployable before the environment variable exists, and clearing the
variable has to be a way back in if the Worker breaks.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

SECRET = "s3cret-value-for-tests"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", SECRET)


@pytest.fixture
def unguarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")


def test_open_when_no_secret_configured(client, unguarded):
    """Deploying the code must not lock anyone out before the variable is set."""
    assert client.get("/api/health/status").status_code == 200


def test_request_without_header_is_rejected(client, guarded):
    r = client.get("/api/health/status")
    assert r.status_code == 403
    assert r.json() == {"detail": "Forbidden"}


def test_request_with_wrong_secret_is_rejected(client, guarded):
    r = client.get("/api/health/status", headers={"X-Proxy-Secret": "wrong"})
    assert r.status_code == 403


def test_request_with_correct_secret_is_served(client, guarded):
    r = client.get("/api/health/status", headers={"X-Proxy-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_a_matching_prefix_is_not_enough(client, guarded):
    r = client.get("/api/health/status", headers={"X-Proxy-Secret": SECRET[:-1]})
    assert r.status_code == 403


def test_non_ascii_header_is_a_403_not_a_500(client, guarded):
    """compare_digest on str raises TypeError outside ASCII; bytes must be used.

    Sent as raw bytes because that is the only way such a header arrives — an
    HTTP client rejects a non-ASCII str before it reaches the wire, but nothing
    stops a hand-built request, and Starlette decodes those bytes as latin-1.
    """
    r = client.get("/api/health/status", headers={"X-Proxy-Secret": b"tajn\xe9"})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/docs", "/openapi.json", "/"])
def test_docs_and_root_are_covered_too(client, guarded, path):
    """/docs is a map of every endpoint, so it must not sit outside the check."""
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", ["/api/apple-health/stats", "/api/manual/family"])
def test_data_endpoints_are_covered(client, guarded, path):
    assert client.get(path).status_code == 403


def test_destructive_endpoint_is_covered(client, guarded):
    assert client.delete("/api/apple-health/data").status_code == 403


def test_chat_endpoint_is_covered(client, guarded):
    """Unauthenticated access here spends real money on the LLM provider."""
    r = client.post("/api/chat/ask", json={"question": "ahoj"})
    assert r.status_code == 403


def test_preflight_is_exempt(client, guarded):
    """Browsers send OPTIONS without custom headers; requiring the secret there
    would fail the preflight before the real request that does carry it."""
    r = client.options(
        "/api/health/status",
        headers={
            "Origin": "https://medicalai.peterholic.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code != 403
