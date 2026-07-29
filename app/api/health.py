import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
async def health_status():
    """Health check — reports whether the database is actually reachable.

    A plain "healthy" that never touches the database hid a broken DATABASE_URL
    for a long time, so the DB check is part of the status, not an extra route.
    """
    database = "ok"
    detail = None
    try:
        from app.database import get_session

        session = get_session()
        try:
            session.execute(text("SELECT 1"))
        finally:
            session.close()
    except Exception as e:
        database = "error"
        detail = str(e)
        logger.error("Health check: database unreachable: %s", e)

    body = {
        "status": "healthy" if database == "ok" else "degraded",
        "service": "MedicalAI Backend",
        "database": database,
    }
    if detail:
        body["database_error"] = detail

    return JSONResponse(status_code=200 if database == "ok" else 503, content=body)
