"""Password hashing and session tokens.

Password hashing is stdlib-only (hashlib.pbkdf2_hmac) rather than pulling in
bcrypt/argon2/passlib: PBKDF2-HMAC-SHA256 at a high iteration count is still an
OWASP-recommended choice, and it means one fewer native dependency in a
container image that already carries tesseract, poppler and friends for OCR.
If this ever needs to change, _HASH_ALGO in the stored string is the version
marker — verify_password dispatches on it, so a future algorithm can be added
without invalidating every existing password.

Session tokens are JWTs (HS256, signed with settings.SECRET_KEY) carried in an
httpOnly cookie — never in localStorage, which a single XSS bug can read in
full. HS256 needs no asymmetric key management and no extra native dependency
(PyJWT's HS256 path is pure Python).
"""
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.config import settings

logger = logging.getLogger(__name__)

_HASH_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 minimum for PBKDF2-HMAC-SHA256
_SALT_BYTES = 16

_JWT_ALGORITHM = "HS256"
_JWT_SUBJECT_CLAIM = "sub"  # user id, as a string (JWT spec requires sub be a string)


def hash_password(password: str) -> str:
    """Return a self-describing hash string: algo$iterations$salt_hex$hash_hex."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_HASH_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time check. Never raises on a malformed stored hash — treat it as a mismatch."""
    try:
        algo, iterations_s, salt_hex, hash_hex = stored_hash.split("$", 3)
        if algo != _HASH_ALGO:
            logger.warning("verify_password: unknown hash algo %r", algo)
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError) as e:
        logger.warning("verify_password: malformed stored hash: %s", e)
        return False

    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        _JWT_SUBJECT_CLAIM: str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    """Return the user id the token was issued for, or None if it is invalid/expired.

    Never raises: an expired, tampered, or garbage cookie value is exactly as
    unauthenticated as no cookie at all, not a 500.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_JWT_ALGORITHM])
        return int(payload[_JWT_SUBJECT_CLAIM])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as e:
        logger.debug("decode_access_token: rejected token: %s", e)
        return None
