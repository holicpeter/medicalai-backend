"""Delete a user's account and every piece of health data it owns.

This is what the GDPR text at registration promises ("súhlas môžete
kedykoľvek odvolať… vymazanie svojich údajov"): withdrawing consent means
the data goes, not that it is hidden. Everything is removed in one
transaction, so a failure halfway leaves the account intact rather than half
deleted, and the uploaded files and in-memory caches are cleared after the
commit.

Not removed, because nothing ties it to a user: the shared nutrition text
cache (a normalized meal description and its AI estimate, see
NutritionTextCache), and the application logs, which carry only numeric ids.
"""
import logging
from pathlib import Path
from typing import Dict, List

from app.config import settings
from app.database import (
    AiUsage,
    AppleHealthData,
    CalendarEvent,
    ChatMessage,
    Document,
    DocumentChunk,
    FamilyMember,
    GarminData,
    HealthRecord,
    NutritionEntry,
    Patient,
    User,
    get_session,
)

logger = logging.getLogger(__name__)

# Every table that holds rows scoped by patient_id. A new patient-owned table
# has to be added here, or deleting an account would leave its rows behind —
# tests/test_account_deletion.py checks this list against the models.
PATIENT_SCOPED = [
    HealthRecord,
    FamilyMember,
    NutritionEntry,
    ChatMessage,
    GarminData,
    CalendarEvent,
    AppleHealthData,
]


def delete_account(user_id: int) -> Dict[str, int]:
    """Remove the user, their patient profile and all data. Returns row counts per table."""
    session = get_session()
    removed: Dict[str, int] = {}
    files: List[str] = []
    patient_id = None
    try:
        patient = session.query(Patient).filter_by(user_id=user_id).first()
        if patient is not None:
            patient_id = patient.id

            documents = session.query(Document).filter_by(patient_id=patient_id).all()
            doc_ids = [d.id for d in documents]
            files = [d.file_path for d in documents if d.file_path]
            if doc_ids:
                removed["document_chunks"] = (
                    session.query(DocumentChunk)
                    .filter(DocumentChunk.document_id.in_(doc_ids))
                    .delete(synchronize_session=False)
                )
            removed["documents"] = (
                session.query(Document).filter_by(patient_id=patient_id).delete(synchronize_session=False)
            )

            for model in PATIENT_SCOPED:
                removed[model.__tablename__] = (
                    session.query(model).filter_by(patient_id=patient_id).delete(synchronize_session=False)
                )

            session.delete(patient)
            removed["patients"] = 1

        removed["ai_usage"] = (
            session.query(AiUsage).filter_by(user_id=user_id).delete(synchronize_session=False)
        )
        removed["users"] = session.query(User).filter_by(id=user_id).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("account deletion failed for user %s — nothing was removed", user_id)
        raise
    finally:
        session.close()

    _remove_files(files)
    if patient_id is not None:
        _drop_caches(patient_id)

    logger.info("auth: deleted user id=%s (patient id=%s): %s", user_id, patient_id, removed)
    return removed


def _remove_files(paths: List[str]) -> None:
    """Best effort: the rows are already gone; a leftover file must not undo that."""
    raw_dir = settings.RAW_DATA_DIR.resolve()
    for raw in paths:
        try:
            path = Path(raw).resolve()
            # Only ever delete inside the upload directory, whatever the row says.
            if raw_dir in path.parents and path.is_file():
                path.unlink()
        except Exception as e:
            logger.warning("account deletion: could not remove file %s: %s", raw, e)


def _drop_caches(patient_id: int) -> None:
    try:
        from app.analysis.trend_analyzer import TrendAnalyzer
        from app.rag import invalidate_cache

        TrendAnalyzer.invalidate_cache(patient_id)
        invalidate_cache(patient_id)
    except Exception as e:
        logger.warning("account deletion: could not clear caches for patient %s: %s", patient_id, e)
