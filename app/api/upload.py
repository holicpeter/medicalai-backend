import asyncio
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List
import shutil
from pathlib import Path
from datetime import datetime

from app.config import settings
from app.ocr.document_processor import DocumentProcessor
from app.ocr.data_extractor import HealthDataExtractor
from app.ocr.csv_importer import CSVImporter

logger = logging.getLogger(__name__)

router = APIRouter()
doc_processor = DocumentProcessor()
data_extractor = HealthDataExtractor()
csv_importer = CSVImporter()

_CONTENT_TYPE_MAP = {
    'application/pdf': '.pdf',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/heic': '.heic',
    'image/heif': '.heif',
    'text/csv': '.csv',
    'application/csv': '.csv',
}

_ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.csv', '.heic', '.heif'}


def _resolve_extension(file: UploadFile) -> str:
    ext = Path(file.filename or '').suffix.lower()
    if ext in _ALLOWED_EXTENSIONS:
        return ext
    ct = (file.content_type or '').split(';')[0].strip().lower()
    return _CONTENT_TYPE_MAP.get(ct, ext)


def _invalidate_analyzers():
    """Clear cached data so next request reloads from DB."""
    from app.analysis.trend_analyzer import TrendAnalyzer
    TrendAnalyzer.invalidate_cache()


async def _process_single_file(file: UploadFile) -> dict:
    """Save and process one uploaded file. Runs Claude call in a thread."""
    file_ext = _resolve_extension(file)

    if file_ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file_ext or 'unknown'}' is not supported. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stem = Path(file.filename or 'document').stem
    safe_filename = f"{timestamp}_{stem}{file_ext}"
    file_path = settings.RAW_DATA_DIR / safe_filename

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    logger.info('Saved upload: %s', safe_filename)

    if file_ext == '.csv':
        health_data = await asyncio.to_thread(csv_importer.import_from_csv, file_path)
        text_content = f"CSV import: {len(health_data)} records"
    else:
        # Run blocking Claude API call in a thread so other files can process concurrently
        text_content = await asyncio.to_thread(doc_processor.process_document, file_path)
        health_data = await asyncio.to_thread(
            data_extractor.extract_health_metrics, text_content, file.filename
        )

    return {
        "filename": safe_filename,
        "original_name": file.filename,
        "extracted_text_length": len(text_content),
        "health_metrics_found": len(health_data),
    }


@router.post("/documents")
async def upload_documents(files: List[UploadFile] = File(...)):
    """
    Upload health documents (PDF, images, CSV) for processing.
    Supported formats: PDF, JPG, JPEG, PNG, HEIC, HEIF, CSV
    Multiple files are processed concurrently.
    """
    try:
        # Process all files concurrently
        tasks = [_process_single_file(f) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        uploaded_files = []
        errors = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                fname = files[i].filename or f"file_{i}"
                logger.error('Failed to process %s: %s', fname, result)
                errors.append({"file": fname, "error": str(result)})
            else:
                uploaded_files.append(result)

        # Invalidate analyzer cache so dashboard reflects new data immediately
        if uploaded_files:
            _invalidate_analyzers()

        if errors and not uploaded_files:
            raise HTTPException(status_code=500, detail=f"All files failed: {errors}")

        return {
            "message": f"Successfully processed {len(uploaded_files)} of {len(files)} files",
            "files": uploaded_files,
            "errors": errors,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Upload failed: %s', e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/csv-template")
async def download_csv_template():
    try:
        template_path = settings.DATA_DIR / "health_data_template.csv"
        csv_importer.create_template_csv(template_path)
        return {
            "message": "CSV template created",
            "path": str(template_path),
            "instructions": "Fill in your health data and upload via /api/upload/documents",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def upload_history():
    """History of processed documents, newest first.

    Built from the extracted records rather than the upload directory, which
    lives on the container filesystem and is wiped on every deploy.
    """
    from sqlalchemy import func
    from app.database import get_session, HealthRecord

    session = get_session()
    try:
        rows = (
            session.query(
                HealthRecord.source_file.label('filename'),
                func.count(HealthRecord.id).label('metrics'),
                func.min(HealthRecord.created_at).label('uploaded_at'),
                func.min(HealthRecord.record_date).label('period_start'),
                func.max(HealthRecord.record_date).label('period_end'),
            )
            .filter(HealthRecord.source == 'ocr')
            .filter(HealthRecord.source_file.isnot(None))
            .group_by(HealthRecord.source_file)
            .order_by(func.min(HealthRecord.created_at).desc())
            .all()
        )

        documents = [
            {
                "filename": r.filename,
                "metrics": r.metrics,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
                "period": {
                    "start": r.period_start.isoformat() if r.period_start else None,
                    "end": r.period_end.isoformat() if r.period_end else None,
                },
            }
            for r in rows
        ]

        # Records extracted before filenames were stored still count, but cannot
        # be attributed to a document — report them rather than hiding them.
        untracked = (
            session.query(func.count(HealthRecord.id))
            .filter(HealthRecord.source == 'ocr')
            .filter(HealthRecord.source_file.is_(None))
            .scalar()
        ) or 0

        return {
            "count": len(documents),
            "documents": documents,
            "metrics_without_source": untracked,
        }
    finally:
        session.close()


@router.get("/documents")
async def list_documents():
    """Files currently on disk. Ephemeral — kept for debugging only."""
    try:
        files = list(settings.RAW_DATA_DIR.glob("*"))
        return {
            "count": len(files),
            "note": "Container storage is wiped on deploy. Use /api/upload/history instead.",
            "files": [
                {"name": f.name, "size": f.stat().st_size, "created": f.stat().st_ctime}
                for f in files if f.is_file()
            ],
        }
    except Exception as e:
        logger.error('Listing documents failed: %s', e)
        raise HTTPException(status_code=500, detail="Could not list documents")
