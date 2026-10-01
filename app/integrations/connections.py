"""Per-user connections to wearables and health services.

How a user connects Withings:
  1. POST /api/connections/withings/connect returns Withings' consent URL with
     a signed `state` naming the user (make_oauth_state).
  2. The user signs in at Withings and allows access.
  3. Withings redirects the browser to the callback with `code` and `state`.
     The callback checks the state AND that the browser's own session is the
     same user (see app/api/integrations.py) — otherwise someone could send a
     victim their own consent link and collect the victim's health data into
     the attacker's account.
  4. The code is exchanged for tokens, stored encrypted per user, and a first
     sync runs in the background. After that, sync_all_connections() keeps
     data fresh every few hours.
"""
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from sqlalchemy import inspect, text

from app.config import settings
from app.database import Patient, User, WearableConnection, get_session
from app.integrations import token_crypto

logger = logging.getLogger(__name__)

_STATE_PURPOSE = "wearable-connect"
_STATE_TTL = timedelta(minutes=15)

# What the "Prepojenia" page shows. `status` is "available" (can be connected
# here), "import" (data comes in through an upload page) or "soon".
PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "withings",
        "name": "Withings",
        "devices": "ScanWatch, Body Scan a ďalšie váhy, tlakomery a hodinky Withings",
        "status": "available",
    },
    {
        "id": "apple_health",
        "name": "Apple Health",
        "devices": "iPhone a Apple Watch",
        "status": "import",
        "path": "/apple-health",
    },
    {
        "id": "garmin",
        "name": "Garmin",
        "devices": "hodinky a náramky Garmin",
        "status": "soon",
    },
    {
        "id": "health_connect",
        "name": "Google Health Connect",
        "devices": "Android, Samsung Health, Fitbit, Oura a ďalšie",
        "status": "soon",
    },
]


# ── OAuth state ─────────────────────────────────────────────────────────────

def make_oauth_state(user_id: int, provider: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": _STATE_PURPOSE,
            "provider": provider,
            "nonce": secrets.token_urlsafe(8),
            "iat": now,
            "exp": now + _STATE_TTL,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def read_oauth_state(state: str, provider: str) -> Optional[int]:
    """The user id the state was issued to, or None if it is forged, expired or for another provider."""
    try:
        payload = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("purpose") != _STATE_PURPOSE or payload.get("provider") != provider:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None


# ── Status ──────────────────────────────────────────────────────────────────

def _connection(session, user_id: int, provider: str) -> Optional[WearableConnection]:
    return session.query(WearableConnection).filter_by(user_id=user_id, provider=provider).first()


def list_connections(user_id: int) -> List[Dict[str, Any]]:
    from app.integrations.withings_connector import get_withings_connector

    session = get_session()
    try:
        rows = {r.provider: r for r in session.query(WearableConnection).filter_by(user_id=user_id).all()}
    finally:
        session.close()

    out = []
    for provider in PROVIDERS:
        item = dict(provider)
        row = rows.get(provider["id"])
        item["connected"] = bool(row and row.refresh_token_enc)
        item["connected_at"] = row.connected_at.isoformat() if row and row.connected_at else None
        item["last_sync_at"] = row.last_sync_at.isoformat() if row and row.last_sync_at else None
        item["last_sync_error"] = row.last_sync_error if row else None
        if provider["id"] == "withings" and not get_withings_connector(user_id).is_configured:
            # The server has no Withings app credentials: nothing to connect to.
            item["status"] = "soon"
        out.append(item)
    return out


def disconnect(user_id: int, provider: str) -> bool:
    session = get_session()
    try:
        removed = (session.query(WearableConnection)
                   .filter_by(user_id=user_id, provider=provider)
                   .delete(synchronize_session=False))
        session.commit()
    finally:
        session.close()
    if provider == "withings":
        from app.integrations.withings_connector import forget_connector
        forget_connector(user_id)
    return bool(removed)


def _record_sync(user_id: int, provider: str, error: Optional[str]) -> None:
    session = get_session()
    try:
        row = _connection(session, user_id, provider)
        if row is not None:
            if error is None:
                row.last_sync_at = datetime.now()
            row.last_sync_error = error
            session.commit()
    finally:
        session.close()


# ── Sync ────────────────────────────────────────────────────────────────────

def _patient_id(user_id: int) -> Optional[int]:
    session = get_session()
    try:
        patient = session.query(Patient).filter_by(user_id=user_id).first()
        return patient.id if patient else None
    finally:
        session.close()


async def sync_withings(user_id: int, days: int = 30, hrv_nights: int = 14) -> Dict[str, Any]:
    """Pull Withings data into the user's own health records and note the outcome."""
    from app.integrations.withings_sync import sync_withings_to_db

    patient_id = _patient_id(user_id)
    if patient_id is None:
        raise RuntimeError(f"user {user_id} has no patient profile")
    try:
        result = await sync_withings_to_db(user_id, patient_id, days=days, hrv_nights=hrv_nights)
    except Exception as e:
        _record_sync(user_id, "withings", str(e)[:500])
        raise
    _record_sync(user_id, "withings", None)
    return result


async def initial_sync(user_id: int) -> None:
    """Right after connecting: fetch the recent history. Never raises."""
    try:
        result = await sync_withings(user_id, days=90)
        logger.info("[CONNECTIONS] initial Withings sync for user %s: %s", user_id,
                    {k: result.get(k) for k in ("written", "updated")})
    except Exception as e:
        logger.warning("[CONNECTIONS] initial Withings sync for user %s failed: %s", user_id, e)


async def sync_all_connections(days: int = 7) -> None:
    """One pass over every connected user. Errors are recorded per user, never raised."""
    session = get_session()
    try:
        user_ids = [r.user_id for r in session.query(WearableConnection)
                    .filter_by(provider="withings")
                    .filter(WearableConnection.refresh_token_enc.isnot(None)).all()]
    finally:
        session.close()

    for user_id in user_ids:
        try:
            await sync_withings(user_id, days=days, hrv_nights=3)
        except Exception as e:
            logger.warning("[CONNECTIONS] scheduled Withings sync for user %s failed: %s", user_id, e)
        # Withings allows 120 requests a minute for the whole app.
        await asyncio.sleep(2)


async def periodic_sync_loop(interval_hours: float) -> None:
    """Started from the app's lifespan; cancelled on shutdown."""
    # Not right at boot: a deploy restarts the app, and syncing everyone on
    # every deploy would only burn the Withings rate limit.
    await asyncio.sleep(15 * 60)
    while True:
        try:
            await sync_all_connections()
        except Exception as e:
            logger.error("[CONNECTIONS] scheduled sync pass failed: %s", e)
        await asyncio.sleep(interval_hours * 3600)


# ── One-time move of the pre-multi-user connection ─────────────────────────

def migrate_legacy_withings_tokens() -> Optional[str]:
    """Move the old app-wide Withings tokens to the admin's own connection.

    Before per-user connections there was one row in `withings_tokens`, in
    plain text, used for the whole app. It belongs to the admin (the only
    user back then). Moving it means the admin does not have to reconnect,
    and deleting the old row means no unencrypted token stays behind.
    """
    from app.database.models import _get_engine

    engine = _get_engine()
    if not inspect(engine).has_table("withings_tokens"):
        return None
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT withings_user_id, access_token, refresh_token, expires_at "
            "FROM withings_tokens WHERE id = 1"
        )).first()
    if row is None:
        return None

    session = get_session()
    try:
        admin = None
        for email in settings.ADMIN_EMAILS:
            admin = session.query(User).filter_by(email=email.strip().lower()).first()
            if admin is not None:
                break
        if admin is None:
            return "legacy Withings tokens kept: no admin account exists yet"
        if _connection(session, admin.id, "withings") is None:
            session.add(WearableConnection(
                user_id=admin.id,
                provider="withings",
                external_user_id=str(row[0]) if row[0] is not None else None,
                access_token_enc=token_crypto.encrypt(row[1]),
                refresh_token_enc=token_crypto.encrypt(row[2]),
                expires_at=float(row[3] or 0),
                connected_at=datetime.now(),
            ))
            session.commit()
            moved = f"moved to {admin.email} (user id={admin.id})"
        else:
            moved = f"{admin.email} already had a connection; old tokens discarded"
    finally:
        session.close()

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM withings_tokens WHERE id = 1"))
    return f"legacy Withings tokens {moved}"
