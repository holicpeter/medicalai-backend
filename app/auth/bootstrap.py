"""One-time setup that can run at startup, driven by Railway variables.

Both tasks can also be run by hand (scripts/migrate_multi_user.py,
scripts/seed_demo_account.py). Running them from startup is what lets the
multi-user release be deployed without a terminal — set the variables in the
Railway dashboard, merge, and the next boot does the rest.

- MIGRATE_ADMIN_EMAIL + MIGRATE_ADMIN_PASSWORD: create the admin account and
  link the pre-existing single-user data to it. Does nothing once an account
  with that email exists. It runs before the app serves any request, so nobody
  can register that email first. Delete MIGRATE_ADMIN_PASSWORD afterwards.
- SEED_DEMO_ACCOUNT=true: create the read-only demo account if it is missing.
  An existing demo is left as it is; re-run the script to refresh its dates.
"""
import logging
import os

from app.auth.security import hash_password
from app.database import Document, Patient, User, get_session

logger = logging.getLogger(__name__)


def _backfill_documents(session, patient_id: int) -> int:
    """Attach every un-owned Document row to patient_id.

    Before multi-user the upload flow never set Document.patient_id, so every
    pre-existing report has NULL there. RAG search and the document list are
    now filtered by patient_id, so without this those documents would silently
    stop showing up anywhere — not deleted, just invisible.
    """
    orphans = session.query(Document).filter(Document.patient_id.is_(None)).all()
    for doc in orphans:
        doc.patient_id = patient_id
    if orphans:
        session.commit()
    return len(orphans)


def link_admin_account(email: str, password: str) -> str:
    """Create the admin user and give it the pre-existing patient's data.

    Idempotent: returns without changes when a user with this email exists.
    Returns a one-line description of what happened; raises on failure.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")

    session = get_session()
    try:
        existing = session.query(User).filter_by(email=email).first()
        if existing is not None:
            linked = session.query(Patient).filter_by(user_id=existing.id).first()
            if linked is None:
                return (f"user {email} already exists (id={existing.id}) but has NO linked "
                        "patient — investigate before letting anyone log in as this user")
            return f"user {email} already exists (id={existing.id}) — nothing to do"

        admin = User(email=email, password_hash=hash_password(password), is_active=True)
        session.add(admin)
        session.flush()  # assigns admin.id

        patient = session.query(Patient).filter(Patient.user_id.is_(None)).first()
        if patient is not None:
            patient.user_id = admin.id
            session.commit()
            what = f"linked existing patient id={patient.id}"
        else:
            patient = Patient(user_id=admin.id, first_name="", last_name="")
            session.add(patient)
            session.commit()
            session.refresh(patient)
            what = f"created a new, empty patient id={patient.id} (no existing one was found)"

        docs = _backfill_documents(session, patient.id)
        return f"created user {email} (id={admin.id}), {what}, linked {docs} unowned document(s)"
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_startup_tasks() -> None:
    """Never raises: a failed one-time task must not keep the API from starting."""
    email = os.environ.get("MIGRATE_ADMIN_EMAIL", "").strip()
    password = os.environ.get("MIGRATE_ADMIN_PASSWORD", "")
    if email and password:
        try:
            logger.info("startup: admin migration: %s", link_admin_account(email, password))
            logger.warning(
                "startup: remove MIGRATE_ADMIN_PASSWORD from the Railway variables now "
                "that the admin account exists"
            )
        except Exception as e:
            logger.error("startup: admin migration failed: %s", e)

    if os.environ.get("SEED_DEMO_ACCOUNT", "").strip().lower() in ("1", "true", "yes"):
        try:
            from app.config import settings

            session = get_session()
            try:
                exists = session.query(User.id).filter_by(email=settings.DEMO_EMAIL.lower()).first()
            finally:
                session.close()
            if exists:
                logger.info("startup: demo account already exists — left as it is")
            else:
                # scripts/ is a package at the repo root, next to app/.
                import secrets

                from scripts.seed_demo_account import seed

                result = seed(settings.DEMO_EMAIL.lower(), secrets.token_urlsafe(18))
                logger.info("startup: demo account created: %s", result)
        except Exception as e:
            logger.error("startup: demo seeding failed: %s", e)
