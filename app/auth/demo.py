"""The shared, read-only demo account.

Visitors try the app without registering: POST /api/auth/demo-login signs
them into the account in settings.DEMO_EMAIL, which holds made-up data
(scripts/seed_demo_account.py). Everyone who clicks "try the demo" shares
that one account, so it is read-only: if visitors could upload reports, save
meals or chat, one of them could type in their own real health data and the
next visitor would read it. It also keeps the demo from spending AI credit
on anything but what it shows.
"""
import logging
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth.security import decode_access_token
from app.config import settings
from app.database import User, get_session

logger = logging.getLogger(__name__)

# Header on the 403 so a client can tell "this is the demo" apart from other
# refusals and offer registration instead of an error.
DEMO_READ_ONLY_HEADER = "X-Demo-Read-Only"

DEMO_READ_ONLY_MESSAGE = (
    "Toto je ukážkový účet s vymyslenými údajmi, v ktorom sa nič nedá meniť. "
    "Vytvorte si vlastný účet zdarma a vyskúšajte to na svojich dátach."
)

# Requests a demo session may still make even though they are not GETs:
# they end the demo session or switch to a real account.
_ALLOWED_WRITES = {
    "/api/auth/logout",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/demo-login",
}


def is_demo_email(email: Optional[str]) -> bool:
    return bool(email) and email.lower() in {e.lower() for e in settings.DEMO_EMAILS}


def _token(request: Request) -> Optional[str]:
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if token:
        return token
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def _is_demo_session(request: Request) -> bool:
    token = _token(request)
    if not token:
        return False
    user_id = decode_access_token(token)
    if user_id is None:
        return False
    session = get_session()
    try:
        user = session.query(User.email).filter_by(id=user_id).first()
        return user is not None and is_demo_email(user.email)
    finally:
        session.close()


def _is_write(request: Request) -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        # A GET that asks Claude for a risk analysis still spends credit.
        return request.query_params.get("use_claude", "").lower() in ("1", "true", "yes")
    return request.url.path not in _ALLOWED_WRITES


async def demo_read_only(request: Request, call_next):
    """Refuse anything that would change data or call AI from the demo account."""
    if request.url.path.startswith("/api/") and _is_write(request) and _is_demo_session(request):
        return JSONResponse(
            status_code=403,
            content={"detail": DEMO_READ_ONLY_MESSAGE},
            headers={DEMO_READ_ONLY_HEADER: "1"},
        )
    return await call_next(request)
