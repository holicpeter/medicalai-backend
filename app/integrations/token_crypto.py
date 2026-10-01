"""Encryption at rest for third-party OAuth tokens (Withings and later others).

A refresh token is a standing key to someone's health data at another
service, so a database dump or a leaked backup must not hand those out. The
key is derived from SECRET_KEY, which already has to stay secret for session
cookies; a separate label keeps the two uses apart.

Rotating SECRET_KEY makes stored tokens unreadable. decrypt() then returns
None and the connection shows as needing to be reconnected — users click
"Connect" once more, nothing breaks.
"""
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_LABEL = b"medicalai/wearable-oauth-tokens/v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(_LABEL + settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("token_crypto: a stored token could not be decrypted (SECRET_KEY changed?)")
        return None
