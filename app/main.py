import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.api import health, upload, analysis, predictions, chat, integrations, manual_entry, apple_health
from app.config import settings
from app.database import init_database, create_default_patient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_database()
        create_default_patient()
        logger.info('Database initialized successfully')
    except Exception as e:
        logger.error('Failed to initialize database: %s', e)
    yield


app = FastAPI(
    title="MedicalAI API",
    description="API for medical health analysis and predictions",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=settings.ALLOWED_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(chat.router)
app.include_router(integrations.router)
app.include_router(manual_entry.router)
app.include_router(apple_health.router)


@app.get("/")
async def root():
    return {
        "message": "MedicalAI API",
        "version": "1.0.0",
        "status": "running",
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=True,
    )
