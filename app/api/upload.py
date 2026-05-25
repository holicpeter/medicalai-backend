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
    TrendAnalyzer._data_cache = None
    TrendAnalyzer._cache_timestamp = None


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
        health_data = await asyncio.to_thread(data_extractor.extract_health_metrics, text_content)

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


@router.get("/documents")
async def list_documents():
    try:
        files = list(settings.RAW_DATA_DIR.glob("*"))
        return {
            "count": len(files),
            "files": [
                {"name": f.name, "size": f.stat().st_size, "created": f.stat().st_ctime}
                for f in files if f.is_file()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
