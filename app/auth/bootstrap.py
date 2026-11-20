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
- Always: an existing demo account whose data has gone stale (its newest
  Apple Health day is more than DEMO_MAX_AGE_DAYS old) is re-seeded, so its
  dates move to today. The activity overview shows recent days and the
  nutrition diary shows today; with old dates both screens looked empty.
  demo_refresh_loop repeats the check while the app runs.
- RESET_PASSWORD_EMAIL + RESET_PASSWORD: set a new password for an existing
  account — the way back in when the password is lost, since the app has no
  email-based reset. Applied on every boot while set, so delete both variables
  once you can log in.
- Always: the Withings connection from before per-user connections (one
  app-wide row in withings_tokens) is moved to the admin's own account,
  encrypted, and the old plain-text row is deleted.
"""
import asyncio
import logging
import os
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func

from app.auth.security import hash_password
from app.database import AppleHealthData, Document, Patient, User, get_session

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


def reset_password(email: str, password: str) -> str:
    """Give an existing account a new password. Raises if there is no such account."""
    email = email.strip().lower()
    if len(password) < 10:
        raise ValueError("password must be at least 10 characters")

    session = get_session()
    try:
        user = session.query(User).filter_by(email=email).first()
        if user is None:
            raise ValueError(f"no account with email {email}")
        user.password_hash = hash_password(password)
        session.commit()
        return f"new password set for {email} (id={user.id})"
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DEMO_MAX_AGE_DAYS = 2
DEMO_CHECK_INTERVAL_HOURS = 6


def _demo_newest_day(email: str) -> Optional[date]:
    """Newest Apple Health day in the demo account, or None if there is none."""
    session = get_session()
    try:
        newest = (
            session.query(func.max(AppleHealthData.start_date))
            .join(Patient, Patient.id == AppleHealthData.patient_id)
            .join(User, User.id == Patient.user_id)
            .filter(User.email == email)
            .scalar()
        )
    finally:
        session.close()
    if newest is None:
        return None
    return newest.date() if hasattr(newest, "date") else newest


def demo_is_stale(newest: Optional[date], today: Optional[date] = None,
                  max_age_days: int = DEMO_MAX_AGE_DAYS) -> bool:
    today = today or date.today()
    return newest is None or newest < today - timedelta(days=max_age_days)


def refresh_demo_if_stale() -> str:
    """Re-seed the demo account when its dates have fallen behind. Never raises."""
    try:
        from app.config import settings

        email = settings.DEMO_EMAIL.lower()
        session = get_session()
        try:
            exists = session.query(User.id).filter_by(email=email).first()
        finally:
            session.close()
        if not exists:
            return "no demo account"
        newest = _demo_newest_day(email)
        if not demo_is_stale(newest):
            return f"demo data is current (newest day {newest})"

        import secrets

        from scripts.seed_demo_account import seed

        result = seed(email, secrets.token_urlsafe(18))
        logger.info("demo account re-seeded (newest day was %s): %s", newest, result)
        return "re-seeded"
    except Exception as e:
        logger.error("demo refresh failed: %s", e)
        return f"failed: {e}"


async def demo_refresh_loop(interval_hours: float = DEMO_CHECK_INTERVAL_HOURS) -> None:
    """Started from the app's lifespan; cancelled on shutdown.

    Assumes one web replica: two processes re-seeding at the same moment would
    both delete and recreate the account.
    """
    # First check a minute after boot, off the startup path: re-seeding takes a
    # few seconds and must not delay the API from serving requests.
    await asyncio.sleep(60)
    while True:
        result = await asyncio.to_thread(refresh_demo_if_stale)
        logger.info("demo refresh check: %s", result)
        await asyncio.sleep(interval_hours * 3600)


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

    reset_email = os.environ.get("RESET_PASSWORD_EMAIL", "").strip()
    reset_to = os.environ.get("RESET_PASSWORD", "")
    if reset_email and reset_to:
        try:
            logger.info("startup: password reset: %s", reset_password(reset_email, reset_to))
            logger.warning(
                "startup: remove RESET_PASSWORD_EMAIL and RESET_PASSWORD from the Railway "
                "variables once you can log in — the reset repeats on every boot"
            )
        except Exception as e:
            logger.error("startup: password reset failed: %s", e)

    try:
        from app.integrations.connections import migrate_legacy_withings_tokens
        moved = migrate_legacy_withings_tokens()
        if moved:
            logger.info("startup: %s", moved)
    except Exception as e:
        logger.error("startup: moving the legacy Withings connection failed: %s", e)

    if os.environ.get("SEED_DEMO_ACCOUNT", "").strip().lower() in ("1", "true", "yes"):
        try:
            from app.config import settings

            session = get_session()
            try:
                exists = session.query(User.id).filter_by(email=settings.DEMO_EMAIL.lower()).first()
            finally:
                session.close()
            if exists:
                logger.info("startup: demo account already exists — demo_refresh_loop keeps it current")
            else:
                # scripts/ is a package at the repo root, next to app/.
                import secrets

                from scripts.seed_demo_account import seed

                result = seed(settings.DEMO_EMAIL.lower(), secrets.token_urlsafe(18))
                logger.info("startup: demo account created: %s", result)
        except Exception as e:
            logger.error("startup: demo seeding failed: %s", e)
