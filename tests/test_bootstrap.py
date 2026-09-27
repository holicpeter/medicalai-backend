"""Startup tasks driven by Railway variables (app/auth/bootstrap.py).

The multi-user release is deployed without a terminal: the admin migration
and the demo account run at boot when their variables are set. These tests
check the admin migration links the pre-multi-user data to the new account,
is safe to run on every boot, and that a failure never stops startup.
"""
import uuid

from fastapi.testclient import TestClient

from app.auth import bootstrap
from app.config import settings
from app.database import Document, HealthRecord, Patient, User, get_session
from app.main import app


def _orphan_patient_with_data():
    """A patient as it existed before multi-user: no user_id, data attached."""
    session = get_session()
    try:
        # Other tests leave orphan-free data behind, but make sure this one is
        # the orphan the migration will pick up.
        for p in session.query(Patient).filter(Patient.user_id.is_(None)).all():
            p.user_id = -1
        patient = Patient(first_name="Pôvodný", last_name="Pacient")
        session.add(patient)
        session.flush()
        session.add(HealthRecord(patient_id=patient.id, record_type="lab", source="manual",
                                 metric_type="glucose", value="5.5"))
        session.add(Document(filename="stara-sprava.pdf", file_path=""))
        session.commit()
        return patient.id
    finally:
        session.close()


def test_the_migration_links_existing_data_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    patient_id = _orphan_patient_with_data()
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("MIGRATE_ADMIN_EMAIL", email)
    monkeypatch.setenv("MIGRATE_ADMIN_PASSWORD", "admin-password-123")

    bootstrap.run_startup_tasks()
    bootstrap.run_startup_tasks()  # every boot runs it again

    session = get_session()
    try:
        users = session.query(User).filter_by(email=email).all()
        assert len(users) == 1
        assert session.query(Patient).filter_by(id=patient_id).one().user_id == users[0].id
        assert session.query(Document).filter(Document.patient_id.is_(None)).count() == 0
    finally:
        session.close()

    client = TestClient(app)
    r = client.post("/api/auth/login", json={"email": email, "password": "admin-password-123"})
    assert r.status_code == 200
    assert r.json()["patient_id"] == patient_id
    records = client.get("/api/manual/health-records").json()
    assert any(rec["metric_type"] == "glucose" for rec in records)


def test_a_failing_task_does_not_raise(monkeypatch):
    monkeypatch.setenv("MIGRATE_ADMIN_EMAIL", "not-an-email")
    monkeypatch.setenv("MIGRATE_ADMIN_PASSWORD", "short")
    bootstrap.run_startup_tasks()  # logs the error, returns normally


def test_the_demo_is_seeded_once_when_asked(monkeypatch):
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "DEMO_EMAIL", email)
    monkeypatch.delenv("MIGRATE_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("SEED_DEMO_ACCOUNT", "true")

    bootstrap.run_startup_tasks()
    session = get_session()
    try:
        first = session.query(User).filter_by(email=email).one().id
    finally:
        session.close()

    bootstrap.run_startup_tasks()  # a second boot leaves it alone
    session = get_session()
    try:
        assert session.query(User).filter_by(email=email).one().id == first
    finally:
        session.close()


def test_a_lost_password_can_be_reset(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    email = f"forgot-{uuid.uuid4().hex[:8]}@example.com"
    client = TestClient(app)
    assert client.post("/api/auth/register", json={
        "email": email, "password": "the-old-password", "gdpr_consent": True,
    }).status_code in (200, 201)

    monkeypatch.setenv("RESET_PASSWORD_EMAIL", email.upper())
    monkeypatch.setenv("RESET_PASSWORD", "a-brand-new-password")
    bootstrap.run_startup_tasks()

    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"email": email, "password": "the-old-password"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": email, "password": "a-brand-new-password"}).status_code == 200


def test_a_reset_for_an_unknown_account_does_not_raise_or_create_one(monkeypatch):
    email = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setenv("RESET_PASSWORD_EMAIL", email)
    monkeypatch.setenv("RESET_PASSWORD", "a-brand-new-password")
    bootstrap.run_startup_tasks()

    session = get_session()
    try:
        assert session.query(User).filter_by(email=email).count() == 0
    finally:
        session.close()
