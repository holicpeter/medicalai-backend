"""GET /api/auth/export returns everything stored about the user, and only theirs."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.account_deletion import PATIENT_SCOPED
from app.config import settings
from app.database import Document, DocumentChunk, get_session
from app.main import app


def _email() -> str:
    return f"export-{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")


def _account(glucose):
    client = TestClient(app)
    r = client.post(
        "/api/auth/register",
        json={"email": _email(), "password": "export-password-1", "gdpr_consent": True},
    )
    assert r.status_code == 201
    client.post(
        "/api/manual/health-record",
        json={"record_date": "2026-01-15", "metric_type": "glucose", "value": glucose},
    )
    return client, r.json()


def test_export_contains_the_users_data_and_nobody_elses():
    client_a, me = _account("5.4")
    _account("9.9")

    session = get_session()
    try:
        doc = Document(patient_id=me["patient_id"], filename="sprava.pdf", file_path="/srv/raw/sprava.pdf")
        session.add(doc)
        session.flush()
        session.add(DocumentChunk(document_id=doc.id, chunk_index=0, text="Záver: v norme"))
        session.commit()
    finally:
        session.close()

    r = client_a.get("/api/auth/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()

    assert body["format"] == "medicalai-export"
    assert body["account"]["email"] == me["email"]
    assert "password_hash" not in body["account"]
    assert body["patient_profile"]["id"] == me["patient_id"]

    for model in PATIENT_SCOPED:
        assert model.__tablename__ in body["data"]
    records = body["data"]["health_records"]
    assert [str(r["value"]) for r in records] == ["5.4"]
    assert all(r["patient_id"] == me["patient_id"] for r in records)

    [document] = body["data"]["documents"]
    assert document["filename"] == "sprava.pdf"
    assert document["text_chunks"] == ["Záver: v norme"]
    assert "file_path" not in document

    for table, rows in body["data"].items():
        assert all(row.get("patient_id") == me["patient_id"] for row in rows), table


def test_export_needs_a_login():
    assert TestClient(app).get("/api/auth/export").status_code == 401
