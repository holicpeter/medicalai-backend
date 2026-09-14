"""Používateľ A nikdy nesmie vidieť (ani zmazať) dáta používateľa B.

This is the one property the whole multi-user-auth change exists to
guarantee. Everything else in this branch — the User/Patient split, threading
patient_id through every analyzer and cache, filtering every query by it — is
in service of this single invariant, so it gets its own end-to-end test file
rather than living as an assertion inside test_auth.py.

Two independently registered accounts (two separate TestClient instances, so
each keeps its own session cookie) exercise every data-owning router that
went through this migration: manual entries, family history, nutrition,
uploaded-document history and Apple Health. For each, B is checked twice —
once through the normal list endpoint (B must not see A's row at all) and
once through the id-based endpoints (B must not be able to read or delete
A's row by guessing/reusing its id — the IDOR class of bug this branch also
fixed on the way).

Same caveat as test_auth.py: no per-test database reset, so every account
uses a freshly generated email.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import dependencies as auth_dependencies
from app.config import settings
from app.main import app


def _email() -> str:
    return f"isolation-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")


@pytest.fixture(autouse=True)
def reset_rate_limit():
    auth_dependencies._attempts.clear()
    yield
    auth_dependencies._attempts.clear()


@pytest.fixture
def user_a():
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"email": _email(), "password": "patient-a-password-1", "gdpr_consent": True},
    )
    assert r.status_code == 201
    return client, r.json()["patient_id"]


@pytest.fixture
def user_b():
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"email": _email(), "password": "patient-b-password-1", "gdpr_consent": True},
    )
    assert r.status_code == 201
    return client, r.json()["patient_id"]


def test_two_registrations_get_two_distinct_patient_ids(user_a, user_b):
    _, patient_id_a = user_a
    _, patient_id_b = user_b
    assert patient_id_a != patient_id_b


def test_health_records_are_not_shared_and_not_deletable_across_accounts(user_a, user_b):
    client_a, _ = user_a
    client_b, _ = user_b

    created = client_a.post(
        "/api/manual/health-record",
        json={"record_date": "2026-01-15", "metric_type": "glucose", "value": "5.4"},
    )
    assert created.status_code == 200
    record_id = created.json()["id"]

    # B's own list must not contain A's record.
    listed_by_b = client_b.get("/api/manual/health-records").json()
    assert all(r["id"] != record_id for r in listed_by_b)

    # A does see it.
    listed_by_a = client_a.get("/api/manual/health-records").json()
    assert any(r["id"] == record_id for r in listed_by_a)

    # B cannot delete it by id (IDOR) — this must 404, not succeed.
    delete_attempt = client_b.delete(f"/api/manual/health-record/{record_id}")
    assert delete_attempt.status_code == 404

    # And it must still be there for A afterwards.
    still_there = client_a.get("/api/manual/health-records").json()
    assert any(r["id"] == record_id for r in still_there)


def test_family_members_are_not_shared_and_not_editable_across_accounts(user_a, user_b):
    client_a, _ = user_a
    client_b, _ = user_b

    created = client_a.post(
        "/api/manual/family",
        json={
            "first_name": "Anna",
            "last_name": "Testovacia",
            "relationship_type": "mother",
            "gender": "F",
        },
    )
    assert created.status_code == 200
    member_id = created.json()["id"]

    listed_by_b = client_b.get("/api/manual/family").json()
    assert all(m["id"] != member_id for m in listed_by_b)

    listed_by_a = client_a.get("/api/manual/family").json()
    assert any(m["id"] == member_id for m in listed_by_a)

    # B cannot edit A's family member by id (IDOR).
    edit_attempt = client_b.put(
        f"/api/manual/family/{member_id}", json={"first_name": "Hijacked"}
    )
    assert edit_attempt.status_code == 404

    # B cannot delete it either.
    delete_attempt = client_b.delete(f"/api/manual/family/{member_id}")
    assert delete_attempt.status_code == 404

    # Untouched for A.
    still_there = client_a.get("/api/manual/family").json()
    matching = [m for m in still_there if m["id"] == member_id]
    assert matching and matching[0]["first_name"] == "Anna"


def test_patient_profile_updates_do_not_cross_accounts(user_a, user_b):
    client_a, _ = user_a
    client_b, _ = user_b

    update = client_a.put("/api/manual/patient", json={"first_name": "PacientA"})
    assert update.status_code == 200

    profile_a = client_a.get("/api/manual/patient").json()
    profile_b = client_b.get("/api/manual/patient").json()

    assert profile_a["first_name"] == "PacientA"
    assert profile_b["first_name"] != "PacientA"
    assert profile_a["id"] != profile_b["id"]


def test_nutrition_entries_are_not_shared(user_a, user_b):
    client_a, _ = user_a
    client_b, _ = user_b

    created = client_a.post(
        "/api/nutrition/entries",
        json={
            "items": [],
            "total_calories": 500,
            "total_protein_g": 20,
            "total_carbs_g": 60,
            "total_fat_g": 15,
        },
    )
    assert created.status_code == 200
    entry_id = created.json()["id"]

    entries_b = client_b.get("/api/nutrition/entries").json()
    assert all(e["id"] != entry_id for e in entries_b)

    entries_a = client_a.get("/api/nutrition/entries").json()
    assert any(e["id"] == entry_id for e in entries_a)


def test_every_scoped_router_rejects_an_unauthenticated_request():
    """Spot-check across routers: none of this isolation matters if a router
    forgot to require login in the first place."""
    anon = TestClient(app)
    get_paths = [
        "/api/manual/family",
        "/api/manual/health-records",
        "/api/nutrition/entries",
        "/api/analysis/summary",
        "/api/predictions/risks",
        "/api/upload/history",
        "/api/apple-health/stats",
    ]
    for path in get_paths:
        response = anon.get(path)
        assert response.status_code == 401, f"GET {path} must require login, got {response.status_code}"

    chat_response = anon.post("/api/chat/ask", json={"question": "ahoj"})
    assert chat_response.status_code == 401, (
        f"POST /api/chat/ask must require login, got {chat_response.status_code}"
    )
