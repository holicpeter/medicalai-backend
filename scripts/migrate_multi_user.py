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

from app.auth.bootstrap import link_admin_account  # noqa: E402
from app.database import init_database  # noqa: E402


def main() -> int:
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")

    if not email:
        email = input("Admin email: ").strip().lower()
    if not password:
        password = getpass.getpass("Admin password (min 10 chars): ")

    init_database()  # idempotent — creates tables/columns/indexes if missing
    try:
        print(link_admin_account(email, password))
    except Exception as e:
        print(f"ERROR: migration failed: {e}", file=sys.stderr)
        return 1
    print(
        "\nSet ADMIN_EMAILS in Railway to include this email so the "
        "Garmin/Withings/Calendar integration endpoints keep working for it "
        "(see app/config.py — those connectors are single-tenant)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
