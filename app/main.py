import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

PROXY_SECRET_HEADER = 'X-Proxy-Secret'


@app.middleware("http")
async def require_proxy_secret(request: Request, call_next):
    """Serve only what came through the Cloudflare Worker.

    Cloudflare Access protects the public domain, but a request sent straight to
    the Railway hostname never passes through Access at all. The Worker attaches
    the shared secret to everything it forwards, so checking it here closes that
    bypass — including for /docs and /openapi.json, which are otherwise a map of
    the whole API.

    A CORS preflight is exempt: browsers send OPTIONS without custom headers, so
    requiring the secret would fail the preflight before the real request that
    does carry it. Nothing is disclosed by an OPTIONS response.
    """
    expected = settings.PROXY_SHARED_SECRET
    if expected and request.method != 'OPTIONS':
        provided = request.headers.get(PROXY_SECRET_HEADER, '')
        # compare_digest, not ==, so a wrong value cannot be found byte by byte
        # from response timing. Compared as bytes because the str form raises
        # TypeError on non-ASCII, which a crafted header would otherwise turn
        # into a 500 instead of the 403 it deserves.
        if not secrets.compare_digest(provided.encode('utf-8', 'surrogateescape'),
                                      expected.encode('utf-8', 'surrogateescape')):
            logger.warning(
                'Rejected %s %s — missing or wrong %s',
                request.method, request.url.path, PROXY_SECRET_HEADER,
            )
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    return await call_next(request)

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
