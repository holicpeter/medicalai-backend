from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # API Settings
    BACKEND_HOST: str = "localhost"
    BACKEND_PORT: int = 8000
    
    # Security
    SECRET_KEY: str = "change-this-in-production"
    
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

# Debug: Check if API key loaded
if settings.ANTHROPIC_API_KEY:
    print(f"[CONFIG] Claude API key loaded: {settings.ANTHROPIC_API_KEY[:20]}...")
else:
    print("[CONFIG] WARNING: Claude API key not found in .env file")

if settings.MISTRAL_API_KEY:
    print(f"[CONFIG] Mistral API key loaded: {settings.MISTRAL_API_KEY[:20]}...")
else:
    print("[CONFIG] WARNING: Mistral API key not found in .env file")

# Ensure directories exist
settings.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
