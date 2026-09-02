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

# Ensure directories exist
settings.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
