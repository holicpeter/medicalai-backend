"""Everything the app stores about one user, as one JSON document.

GDPR gives users the right to get their data in a machine-readable form
(art. 15 access, art. 20 portability). This collects exactly what
account_deletion.delete_account would remove — the same PATIENT_SCOPED list,
so the two cannot drift apart — plus the account itself, without the
password hash.
"""
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import inspect

from app.auth.account_deletion import PATIENT_SCOPED
from app.database import AiUsage, Document, DocumentChunk, Patient, User, get_session

EXPORT_FORMAT_VERSION = 1

# Never leave the server, not even to the account's owner.
_EXCLUDED_COLUMNS = {"password_hash"}


def _value(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None  # NaN/inf are not valid JSON
    return v


def _row(obj) -> Dict[str, Any]:
    # Through the mapper, not __table__.columns: an attribute can be named
    # differently from its column (e.g. a column called "metadata").
    return {
        attr.key: _value(getattr(obj, attr.key))
        for attr in inspect(obj).mapper.column_attrs
        if attr.key not in _EXCLUDED_COLUMNS
    }


def _rows(objs) -> List[Dict[str, Any]]:
    return [_row(o) for o in objs]


def export_account(user_id: int) -> Dict[str, Any]:
    session = get_session()
    try:
        user = session.query(User).filter_by(id=user_id).one()
        patient = session.query(Patient).filter_by(user_id=user_id).first()

        data: Dict[str, Any] = {}
        documents: List[Dict[str, Any]] = []
        if patient is not None:
            for model in PATIENT_SCOPED:
                data[model.__tablename__] = _rows(
                    session.query(model).filter_by(patient_id=patient.id).order_by(model.id).all()
                )
            for doc in session.query(Document).filter_by(patient_id=patient.id).order_by(Document.id).all():
                entry = _row(doc)
                # The server-side path says nothing useful to the user.
                entry.pop("file_path", None)
                entry["text_chunks"] = [
                    chunk.text
                    for chunk in session.query(DocumentChunk)
                    .filter_by(document_id=doc.id)
                    .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
                    .all()
                ]
                documents.append(entry)
        data["documents"] = documents

        return {
            "format": "medicalai-export",
            "format_version": EXPORT_FORMAT_VERSION,
            "exported_at": datetime.now().isoformat(),
            "account": _row(user),
            "patient_profile": _row(patient) if patient is not None else None,
            "data": data,
            "ai_usage": _rows(
                session.query(AiUsage).filter_by(user_id=user_id).order_by(AiUsage.day).all()
            ),
        }
    finally:
        session.close()
