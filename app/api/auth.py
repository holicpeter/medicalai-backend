"""Registration and login.

Open registration on purpose (no invite code) — the point of this change is
to let more testers into the app quickly. See
claude/prompt-multi-profil-rodina.md in the project for that tradeoff and
when to revisit it (an invite code becomes worth adding once the tester count
outgrows what open signup can absorb, or abuse shows up in practice).

Every user gets exactly one Patient row, created here at registration time —
that is what every other scoped endpoint in the app resolves through
app.auth.dependencies.get_current_patient_id.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import (
    check_rate_limit,
    clear_auth_cookie,
    get_current_user,
    set_auth_cookie,
)
from app.auth.account_deletion import delete_account
from app.auth.account_export import export_account
from app.auth.quota import is_unlimited, resets_at, usage_summary
from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import Patient, User, get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MIN_PASSWORD_LENGTH = 10

# Deliberately simple (not the full RFC 5322 grammar): pydantic's EmailStr
# would need the email-validator package, one more dependency for a check
# that only has to reject obvious typos — the real proof an address works is
# the account being usable, not a stricter regex. Same tradeoff already made
# for password hashing (stdlib PBKDF2 instead of bcrypt/passlib) and JWT
# (PyJWT's dependency-free HS256 path).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value) or len(value) > 255:
        raise ValueError("Neplatná emailová adresa.")
    return value


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=_MIN_PASSWORD_LENGTH, max_length=200)
    # Explicit, separate from "I agree to the ToS" — this app stores health
    # data, so consent has to be about that specifically and per-user, not
    # bundled into a generic checkbox. Required: True, not just present.
    gdpr_consent: bool

    _normalize_email = field_validator("email")(_validate_email)

    @field_validator("gdpr_consent")
    @classmethod
    def _consent_must_be_given(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "Súhlas so spracovaním zdravotných údajov je potrebný na vytvorenie účtu."
            )
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    _normalize_email = field_validator("email")(_validate_email)


class UserOut(BaseModel):
    id: int
    email: str
    patient_id: int
    created_at: str
    # Only for the mobile app (see _wants_token); the web never gets it.
    token: Optional[str] = None


# The native mobile app asks for the token in the response body with this
# header, stores it in the Keychain/Keystore and sends it back as a Bearer
# header. The web app never sends it, so for the web the token stays in the
# httpOnly cookie and no page script can read it. A password is needed to get
# a body token at all, so an XSS on the web page cannot mint one from the
# cookie alone.
_TOKEN_MODE_HEADER = "x-auth-mode"


def _wants_token(request: Request) -> bool:
    return request.headers.get(_TOKEN_MODE_HEADER, "").strip().lower() == "token"


def _issue_session(request: Request, response: Response, user: User, patient_id: int) -> UserOut:
    token = create_access_token(user.id)
    out = _serialize(user, patient_id)
    if _wants_token(request):
        out.token = token
    else:
        set_auth_cookie(response, token)
    return out


def _serialize(user: User, patient_id: int) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        patient_id=patient_id,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.post("/register", response_model=UserOut, response_model_exclude_none=True, status_code=201)
async def register(data: RegisterRequest, request: Request, response: Response):
    check_rate_limit(request, bucket="register")

    session = get_session()
    try:
        email = data.email.lower().strip()
        existing = session.query(User).filter_by(email=email).first()
        if existing is not None:
            # Same message as "wrong password" would not help here — the
            # thing this guards is a health app, and confirming account
            # existence by email is a low-severity leak next to it being the
            # only way to give a useful error to someone who signed up
            # before and forgot. Rate limiting above is what keeps this from
            # being an enumeration oracle at scale.
            raise HTTPException(status_code=409, detail="Účet s týmto emailom už existuje.")

        user = User(
            email=email,
            password_hash=hash_password(data.password),
            gdpr_consent_at=datetime.now(),
            is_active=True,
        )
        session.add(user)
        session.flush()  # assigns user.id

        # Every user owns exactly one Patient — their own profile. Created
        # empty here; the onboarding screens (health card scan, family
        # history, Apple Health, nutrition) fill it in from a clean slate,
        # the same shape the admin's own profile has.
        patient = Patient(user_id=user.id, first_name="", last_name="")
        session.add(patient)
        session.commit()
        session.refresh(user)
        session.refresh(patient)

        logger.info("auth: registered user id=%s", user.id)
        return _issue_session(request, response, user, patient.id)
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.exception("register failed")
        raise HTTPException(status_code=500, detail="Registrácia zlyhala.") from e
    finally:
        session.close()


@router.post("/login", response_model=UserOut, response_model_exclude_none=True)
async def login(data: LoginRequest, request: Request, response: Response):
    check_rate_limit(request, bucket="login")

    session = get_session()
    try:
        email = data.email.lower().strip()
        user = session.query(User).filter_by(email=email).first()

        # Verify against a real hash either way, so a request for a
        # nonexistent email does not return faster than one for a real
        # email with a wrong password — that timing difference is itself an
        # account-enumeration channel.
        password_hash = user.password_hash if user is not None else (
            "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
        )
        password_ok = verify_password(data.password, password_hash)

        if user is None or not password_ok or not user.is_active:
            raise HTTPException(status_code=401, detail="Nesprávny email alebo heslo.")

        patient = session.query(Patient).filter_by(user_id=user.id).first()
        if patient is None:
            logger.error("login: user %s has no linked Patient row", user.id)
            raise HTTPException(status_code=500, detail="Profil sa nenašiel. Kontaktujte podporu.")

        logger.info("auth: logged in user id=%s", user.id)
        return _issue_session(request, response, user, patient.id)
    except HTTPException:
        raise
    finally:
        session.close()


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookie(response)
    return {"success": True}


@router.get("/me", response_model=UserOut, response_model_exclude_none=True)
async def me(current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        patient = session.query(Patient).filter_by(user_id=current_user.id).first()
        if patient is None:
            raise HTTPException(status_code=500, detail="Profil sa nenašiel. Kontaktujte podporu.")
        return _serialize(current_user, patient.id)
    finally:
        session.close()


@router.get("/usage")
async def usage(current_user: User = Depends(get_current_user)):
    """Today's free AI allowance, for the account screen (web and mobile)."""
    items = usage_summary(current_user)
    return {
        "items": items,
        "any_exhausted": any(item["exhausted"] for item in items),
        "unlimited": all(item["limit"] is None for item in items),
        "resets_at": resets_at().isoformat(),
    }


class DeleteAccountRequest(BaseModel):
    password: str


@router.post("/delete-account")
async def delete_my_account(
    data: DeleteAccountRequest,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Permanently delete the logged-in account and all of its health data.

    The password is asked again: a session alone (a borrowed unlocked phone,
    a stolen cookie) must not be enough to wipe someone's records for good.
    """
    check_rate_limit(request, bucket="delete-account")

    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Nesprávne heslo.")

    # The admin account also holds the Garmin/Withings/Calendar connections
    # for the whole app; deleting it by accident from a phone would take
    # those down for everyone. Remove the email from ADMIN_EMAILS first.
    if is_unlimited(current_user):
        raise HTTPException(
            status_code=403,
            detail="Administrátorský účet sa nedá zmazať v aplikácii. "
                   "Najprv odstráňte email z ADMIN_EMAILS.",
        )

    if current_user.email.lower() in {e.lower() for e in settings.DEMO_EMAILS}:
        raise HTTPException(
            status_code=403,
            detail="Ukážkový účet sa nedá zmazať — zdieľajú ho všetci návštevníci. "
                   "Vo vlastnom účte táto funkcia funguje.",
        )

    try:
        delete_account(current_user.id)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Zmazanie účtu zlyhalo, nič sa nezmazalo. Skúste to znova.",
        ) from e

    clear_auth_cookie(response)
    return {"success": True}


@router.get("/export")
async def export_my_data(request: Request, current_user: User = Depends(get_current_user)):
    """Download everything stored about the logged-in user as one JSON file.

    Rate-limited: for an account with a large Apple Health import this is a
    heavy query, and nobody needs more than a few exports in five minutes.
    """
    check_rate_limit(request, bucket="export")
    data = await asyncio.to_thread(export_account, current_user.id)
    filename = f"medicalai-export-{datetime.now():%Y-%m-%d}.json"
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
