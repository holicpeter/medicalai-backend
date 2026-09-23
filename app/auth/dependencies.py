"""FastAPI dependencies that turn a request into "which patient is this".

Every data-owning endpoint in the app used to answer that question with
`session.query(Patient).first()` — there was only ever one. These dependencies
are the replacement: `get_current_patient_id` is what every scoped endpoint
now depends on instead, and it is the single place that maps a session cookie
to a patient_id. Get this one function wrong and every table filtered by
patient_id downstream leaks across accounts, so it is deliberately small and
has no fallback that guesses a patient when the token is missing or invalid.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, Response

from app.auth.security import decode_access_token
from app.config import settings
from app.database import Patient, User, get_session

logger = logging.getLogger(__name__)


def _bearer_token(request: Request) -> Optional[str]:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def get_current_user(request: Request) -> User:
    """The logged-in user, or 401.

    The web app sends the JWT in the httpOnly cookie, so page scripts never
    see it. The native mobile app has no cookie jar worth relying on and keeps
    the token in the Keychain/Keystore instead, sending it as
    `Authorization: Bearer <token>` — same token, same signature check. The
    cookie wins when both are present.
    """
    token = request.cookies.get(settings.AUTH_COOKIE_NAME) or _bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Neprihlásený.")

    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Neplatné alebo expirované prihlásenie.")

    session = get_session()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Neplatné alebo expirované prihlásenie.")
        # Detached from the session on purpose — the caller gets a plain
        # object with the fields already loaded, not a live ORM instance tied
        # to a session that is about to close underneath it.
        session.expunge(user)
        return user
    finally:
        session.close()


def get_current_patient_id(user: User = Depends(get_current_user)) -> int:
    """The id of the current user's own Patient row.

    Every user gets exactly one Patient, created at registration (see
    app/api/auth.py) — so a missing one here means the data model was
    violated somewhere else, not a normal "no profile yet" state, and the 500
    is deliberate: silently creating one here would hide that bug instead of
    surfacing it.
    """
    session = get_session()
    try:
        patient = session.query(Patient).filter_by(user_id=user.id).first()
        if patient is None:
            logger.error("get_current_patient_id: user %s has no linked Patient row", user.id)
            raise HTTPException(
                status_code=500,
                detail="Profil sa nenašiel. Kontaktujte podporu.",
            )
        return patient.id
    finally:
        session.close()


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for the Garmin/Withings/Calendar integration endpoints.

    Those connectors keep one OAuth session per process (see
    app/integrations/*_connector.py) — not per user — because they were built
    before there was more than one user. Letting any authenticated tester
    call them would mean either reading the admin's own wearable data through
    a request that is authenticated as someone else, or overwriting the
    admin's connector session entirely. Restricting them to ADMIN_EMAILS is
    the stopgap until those integrations store a token per user.
    """
    if user.email.lower() not in {e.lower() for e in settings.ADMIN_EMAILS}:
        raise HTTPException(
            status_code=403,
            detail=(
                "Táto integrácia je zatiaľ dostupná len pre administrátora účtu "
                "(Garmin/Withings/Kalendár zdieľajú jedno pripojenie pre celú appku)."
            ),
        )
    return user


# ─── Rate limiting for /api/auth/register and /api/auth/login ──────────────
#
# In-memory, per-process, sliding window. Not shared across Railway
# instances or survivable across a restart — a real deployment with more than
# one worker would want this in the database or Redis instead. For the
# tester-onboarding phase this is scoped to a single Railway service with one
# process, and the goal is only to blunt casual brute-forcing and signup
# spam, not to be an airtight rate limiter.

_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 300  # 5 minutes

_attempts: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # This API sits behind a Cloudflare Worker (see require_proxy_secret in
    # main.py) which does not currently forward the original client IP, so
    # this falls back to the immediate peer — the Worker's own address if it
    # never sets one. That makes this limiter key mostly by "everyone behind
    # the Worker" rather than by individual client, which is a known
    # limitation, not an oversight: it still catches a single script hammering
    # /register in a loop, which is the main risk during open registration.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, bucket: str) -> None:
    """Raise 429 if this (ip, bucket) pair has exceeded the attempt budget.

    Call this before doing any password verification work — the point is to
    stop a loop of attempts from reaching bcrypt-equivalent-cost hashing at
    all, not just to stop it from succeeding.
    """
    key = (_client_ip(request), bucket)
    now = time.monotonic()
    window = _attempts[key]

    while window and now - window[0] > _WINDOW_SECONDS:
        window.popleft()

    if len(window) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Príliš veľa pokusov. Skúste to znova o pár minút.",
        )

    window.append(now)


def _cookie_samesite() -> str:
    # The frontend (medicalai.peterholic.com / a *.vercel.app preview) and
    # this API (fronted by the Cloudflare Worker on its own domain) are
    # different sites, so a fetch()-attached cookie needs SameSite=None —
    # SameSite=Lax is only sent on top-level navigation, not on the
    # cross-origin XHR/fetch calls the SPA makes. None requires Secure, which
    # is why this only applies when AUTH_COOKIE_SECURE is on; local
    # http://localhost dev (frontend and backend both on "localhost", just
    # different ports — same site by the Secure-cookie definition) falls
    # back to Lax, since a browser refuses SameSite=None without Secure.
    return "none" if settings.AUTH_COOKIE_SECURE else "lax"


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=_cookie_samesite(),
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        path="/",
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=_cookie_samesite(),
    )
