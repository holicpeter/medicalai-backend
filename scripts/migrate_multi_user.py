"""One-time migration: link the pre-existing single Patient row to a new admin User.

Run this ONCE, after deploying the code in this branch but before testers start
registering. It is safe to run again (idempotent) — it does nothing if a User
already owns the existing Patient row.

Usage (Railway): set ADMIN_EMAIL and ADMIN_PASSWORD as one-off env vars (or
export them locally against DATABASE_URL) and run:

    python -m scripts.migrate_multi_user

What it does:
  1. Ensures the `users` table and `patients.user_id` column exist (normally
     already true — app.database.models.init_database() does this on every
     startup — but this script does not assume the app has started yet).
  2. If a User with ADMIN_EMAIL already exists, stops (already migrated).
  3. Otherwise creates that User, and links the *first* Patient row with no
     user_id (there should be at most one — this app has always had exactly
     one implicit patient) to it.
  4. If there is no existing Patient row at all (a brand new database), just
     creates the User; that account's own Patient row will be created the
     normal way the next time someone logs in — actually no: registration is
     what creates Patient rows now, so this creates one directly, matching
     what app/api/auth.py's register() does.

Existing data (health_records, documents, apple_health_data, nutrition_entries,
family_members, chat_messages) is NOT touched — it already carries
patient_id pointing at the row this script links, so it becomes that admin
user's data automatically, with nothing to copy or rewrite.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.security import hash_password  # noqa: E402
from app.database import Document, Patient, User, get_session, init_database  # noqa: E402


def _backfill_documents(session, patient_id: int) -> int:
    """Attach every un-owned Document row to patient_id.

    Document.patient_id is nullable and, before this branch, was never set by
    the upload flow at all (see app/rag/retriever.py's index_document) — every
    pre-existing uploaded report has patient_id = NULL. RAG search and the
    document inventory are now filtered by patient_id (per-patient cache and
    IDOR fix), so without this step those documents would silently stop
    showing up anywhere — not deleted, just invisible.
    """
    orphans = session.query(Document).filter(Document.patient_id.is_(None)).all()
    for doc in orphans:
        doc.patient_id = patient_id
    if orphans:
        session.commit()
    return len(orphans)


def main() -> int:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")

    if not email:
        email = input("Admin email: ").strip().lower()
    if not password:
        password = getpass.getpass("Admin password (min 10 chars): ")

    if not email or "@" not in email:
        print("ERROR: a valid email is required.", file=sys.stderr)
        return 1
    if len(password) < 10:
        print("ERROR: password must be at least 10 characters.", file=sys.stderr)
        return 1

    init_database()  # idempotent — creates tables/columns/indexes if missing

    session = get_session()
    try:
        existing_user = session.query(User).filter_by(email=email).first()
        if existing_user is not None:
            print(f"User {email} already exists (id={existing_user.id}) — nothing to do.")
            linked = session.query(Patient).filter_by(user_id=existing_user.id).first()
            if linked is None:
                print(
                    "WARNING: that user has no linked Patient row. This should not "
                    "happen — investigate before letting anyone log in as this user."
                )
            return 0

        admin = User(
            email=email,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(admin)
        session.flush()  # assigns admin.id

        orphan_patient = session.query(Patient).filter(Patient.user_id.is_(None)).first()
        if orphan_patient is not None:
            orphan_patient.user_id = admin.id
            session.commit()
            print(
                f"Created user {email} (id={admin.id}) and linked existing patient "
                f"id={orphan_patient.id} ({orphan_patient.first_name} "
                f"{orphan_patient.last_name}).".strip()
            )
            doc_count = _backfill_documents(session, orphan_patient.id)
            if doc_count:
                print(f"Linked {doc_count} previously unowned document(s) to this patient.")
        else:
            new_patient = Patient(user_id=admin.id, first_name="", last_name="")
            session.add(new_patient)
            session.commit()
            session.refresh(new_patient)
            print(
                f"Created user {email} (id={admin.id}) with a new, empty patient "
                f"profile (id={new_patient.id}) — no pre-existing patient row was found."
            )
            doc_count = _backfill_documents(session, new_patient.id)
            if doc_count:
                print(f"Linked {doc_count} previously unowned document(s) to this patient.")

        print(
            "\nSet ADMIN_EMAILS in Railway to include this email so the "
            "Garmin/Withings/Calendar integration endpoints keep working for it "
            "(see app/config.py — those connectors are single-tenant)."
        )
        return 0
    except Exception as e:
        session.rollback()
        print(f"ERROR: migration failed: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
