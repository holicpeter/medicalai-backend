"""Deleting an account removes the account and all of its data, and nobody else's.

The GDPR text at registration promises users can withdraw consent and have
their data erased; POST /api/auth/delete-account is how. These tests create
data through the real endpoints, delete one of two accounts, and check the
database directly: the deleted patient has no rows left in any table, while
the other account is untouched.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.account_deletion import PATIENT_SCOPED
from app.config import settings
from app.database import Base, Document, DocumentChunk, Patient, User, get_session
from app.main import app

_PASSWORD = "delete-me-password-1"


def _email() -> str:
    return f"delete-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [])


def _account(email=None):
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"email": email or _email(), "password": _PASSWORD, "gdpr_consent": True},
    )
    assert r.status_code == 201
    body = r.json()
    client.post(
        "/api/manual/health-record",
        json={"record_date": "2026-01-15", "metric_type": "glucose", "value": "5.4"},
    )
    return client, body["id"], body["patient_id"]


def _add_document(patient_id):
    session = get_session()
    try:
        doc = Document(patient_id=patient_id, filename="sprava.pdf", file_path="/nonexistent/sprava.pdf")
        session.add(doc)
        session.flush()
        session.add(DocumentChunk(document_id=doc.id, text="text správy"))
        session.commit()
        return doc.id
    finally:
        session.close()


def _rows_for(patient_id):
    session = get_session()
    try:
        counts = {
            model.__tablename__: session.query(model).filter_by(patient_id=patient_id).count()
            for model in PATIENT_SCOPED
        }
        counts["documents"] = session.query(Document).filter_by(patient_id=patient_id).count()
        counts["patients"] = session.query(Patient).filter_by(id=patient_id).count()
        return counts
    finally:
        session.close()


def test_every_patient_scoped_table_is_covered():
    """A new table with a patient_id column must be added to PATIENT_SCOPED."""
    scoped = {
        table.name
        for table in Base.metadata.sorted_tables
        if "patient_id" in table.columns and table.name != "documents"
    }
    assert scoped == {model.__tablename__ for model in PATIENT_SCOPED}


def test_deleting_removes_the_account_and_all_its_data_and_nothing_else():
    client_a, user_a, patient_a = _account()
    client_b, user_b, patient_b = _account()
    doc_a = _add_document(patient_a)
    _add_document(patient_b)
    assert _rows_for(patient_a)["health_records"] == 1

    r = client_a.post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 200

    assert all(count == 0 for count in _rows_for(patient_a).values()), _rows_for(patient_a)
    session = get_session()
    try:
        assert session.query(User).filter_by(id=user_a).count() == 0
        assert session.query(DocumentChunk).filter_by(document_id=doc_a).count() == 0
    finally:
        session.close()

    # B is untouched.
    rows_b = _rows_for(patient_b)
    assert rows_b["health_records"] == 1 and rows_b["documents"] == 1 and rows_b["patients"] == 1
    assert client_b.get("/api/auth/me").status_code == 200

    # A's session is gone and the email can be registered again from scratch.
    assert client_a.get("/api/auth/me").status_code == 401


def test_a_wrong_password_deletes_nothing():
    client, user_id, patient_id = _account()
    r = client.post("/api/auth/delete-account", json={"password": "not-my-password"})
    assert r.status_code == 401
    assert _rows_for(patient_id)["health_records"] == 1
    assert client.get("/api/auth/me").status_code == 200


def test_it_needs_a_login():
    r = TestClient(app).post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 401


def test_the_admin_account_cannot_be_deleted_from_the_app(monkeypatch):
    email = _email()
    client, _, patient_id = _account(email)
    monkeypatch.setattr(settings, "ADMIN_EMAILS", [email])

    r = client.post("/api/auth/delete-account", json={"password": _PASSWORD})
    assert r.status_code == 403
    assert _rows_for(patient_id)["patients"] == 1


def test_the_mobile_bearer_token_can_delete_its_account():
    email = _email()
    r = TestClient(app).post(
        "/api/auth/register",
        json={"email": email, "password": _PASSWORD, "gdpr_consent": True},
        headers={"X-Auth-Mode": "token"},
    )
    auth = {"Authorization": f"Bearer {r.json()['token']}"}
    mobile = TestClient(app)

    assert mobile.post("/api/auth/delete-account", json={"password": _PASSWORD}, headers=auth).status_code == 200
    assert mobile.get("/api/auth/me", headers=auth).status_code == 401
