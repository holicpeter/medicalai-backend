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
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import (
    check_rate_limit,
    clear_auth_cookie,
    get_current_user,
    set_auth_cookie,
)
from app.auth.security import create_access_token, hash_password, verify_password
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


def _serialize(user: User, patient_id: int) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        patient_id=patient_id,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


@router.post("/register", response_model=UserOut, status_code=201)
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

        token = create_access_token(user.id)
        set_auth_cookie(response, token)

        logger.info("auth: registered user id=%s", user.id)
        return _serialize(user, patient.id)
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        logger.exception("register failed")
        raise HTTPException(status_code=500, detail="Registrácia zlyhala.") from e
    finally:
        session.close()


@router.post("/login", response_model=UserOut)
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

        token = create_access_token(user.id)
        set_auth_cookie(response, token)

        logger.info("auth: logged in user id=%s", user.id)
        return _serialize(user, patient.id)
    except HTTPException:
        raise
    finally:
        session.close()


@router.post("/logout")
async def logout(response: Response):
    clear_auth_cookie(response)
    return {"success": True}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    session = get_session()
    try:
        patient = session.query(Patient).filter_by(user_id=current_user.id).first()
        if patient is None:
            raise HTTPException(status_code=500, detail="Profil sa nenašiel. Kontaktujte podporu.")
        return _serialize(current_user, patient.id)
    finally:
        session.close()
