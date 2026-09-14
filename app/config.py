import logging
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_KEY = "change-this-in-production"


class Settings(BaseSettings):
    # API Settings
    BACKEND_HOST: str = "localhost"
    BACKEND_PORT: int = 8000

    # Security
    SECRET_KEY: str = _DEFAULT_SECRET_KEY

    # Auth — JWT session cookie (HS256, signed with SECRET_KEY).
    #
    # Registration is open (no invite code) by design for this phase: the
    # goal is letting more testers in quickly, not gatekeeping them. See
    # claude/prompt-multi-profil-rodina.md in the project for the tradeoff —
    # revisit if the tester count grows past what a few dozen open signups
    # can absorb.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14  # 14 days
    AUTH_COOKIE_NAME: str = "medicalai_session"
    # False only for local http://localhost development; Railway/Cloudflare
    # serve https, so this must be True there or the cookie is silently
    # dropped by the browser over http and, worse, would be sent in the
    # clear if it somehow were not.
    AUTH_COOKIE_SECURE: bool = True
    # Emails allowed to call the Garmin/Withings/Calendar integration
    # endpoints. Those connectors hold one OAuth session per process (see
    # app/integrations/*_connector.py) — they were never built to be
    # multi-tenant, and making them so means storing per-user OAuth tokens in
    # the database, which is out of scope here. Restricting them to the
    # admin's own email is what stops a second tester from either reading
    # the admin's Withings data through an authenticated-but-wrong-tenant
    # request, or silently overwriting the admin's connector session.
    ADMIN_EMAILS: List[str] = []

    # Shared secret with the Cloudflare Worker that fronts this API.
    #
    # Cloudflare Access guards medicalai.peterholic.com, but this Railway
    # hostname is public and Access never sees a request sent straight to it.
    # When this is set, only requests carrying it in X-Proxy-Secret are served,
    # which makes the Worker the sole route in.
    #
    # Empty means the check is off. That default is deliberate: the code can be
    # deployed before the variable exists without locking anyone out, and
    # clearing the variable is the way back in if the Worker ever breaks.
    PROXY_SHARED_SECRET: str = ""

    # CORS — override via ALLOWED_ORIGINS env var (JSON array or comma-separated)
    ALLOWED_ORIGINS: List[str] = [
        "https://medicalai.peterholic.com",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost:5173",
    ]

    # Vercel deployment URLs for this project (production alias + per-deploy previews).
    # Scoped to the medicalai-* project so it is not an open allowlist for all of vercel.app.
    ALLOWED_ORIGIN_REGEX: str = r"https://medicalai[a-z0-9-]*\.vercel\.app"

    # Claude API
    ANTHROPIC_API_KEY: str = ""

    # Mistral API
    MISTRAL_API_KEY: str = ""

    # Withings API (ScanWatch 2 + Body Scan)
    #
    # WITHINGS_REDIRECT_URI must match the Callback URL registered at
    # developer.withings.com character for character, or the token exchange
    # fails with a status the HTTP layer reports as 200.
    WITHINGS_CLIENT_ID: str = ""
    WITHINGS_CLIENT_SECRET: str = ""
    WITHINGS_REDIRECT_URI: str = ""

    # OCR Settings
    TESSERACT_LANG: str = "slk"

    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    RAW_DATA_DIR: Path = DATA_DIR / "raw"
    PROCESSED_DATA_DIR: Path = DATA_DIR / "processed"
    MODELS_DIR: Path = DATA_DIR / "models"

    # ML Settings
    MODEL_RETRAIN_THRESHOLD: float = 0.85

    class Config:
        env_file = str(Path(__file__).parent.parent / ".env")
        case_sensitive = True
        env_file_encoding = 'utf-8'
        # An env var that no field here declares must not take the whole app
        # down at import time. Without this, adding a variable in Railway
        # before the matching field exists crashes every deploy — Settings()
        # runs at module import, so FastAPI never starts.
        extra = "ignore"


settings = Settings()

if settings.SECRET_KEY == _DEFAULT_SECRET_KEY:
    logger.warning('SECRET_KEY is set to the insecure default — set SECRET_KEY in your .env file')

if settings.PROXY_SHARED_SECRET:
    logger.info('Proxy shared secret loaded — direct requests will be rejected')
else:
    logger.warning(
        'PROXY_SHARED_SECRET is not set — this API is reachable by anyone who '
        'knows its hostname'
    )

if settings.ANTHROPIC_API_KEY:
    logger.info('Claude API key loaded')
else:
    logger.warning('ANTHROPIC_API_KEY not found in .env file')

if settings.MISTRAL_API_KEY:
    logger.info('Mistral API key loaded')
else:
    logger.warning('MISTRAL_API_KEY not found in .env file')

if settings.WITHINGS_CLIENT_ID and settings.WITHINGS_CLIENT_SECRET:
    logger.info('Withings credentials loaded')
else:
    logger.warning('WITHINGS_CLIENT_ID / WITHINGS_CLIENT_SECRET not set — Withings sync disabled')

if settings.ADMIN_EMAILS:
    logger.info('Admin emails loaded (%d) — Garmin/Withings/Calendar restricted to them', len(settings.ADMIN_EMAILS))
else:
    logger.warning(
        'ADMIN_EMAILS is not set — every /api/integrations/{garmin,withings,calendar} '
        'endpoint will 503 rather than silently share one tenant\'s connector session'
    )

# Ensure directories exist
settings.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
