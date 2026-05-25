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

# Maps content-type → canonical extension (used when filename has no extension)
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
    """Return the lowercase extension, falling back to content-type if missing."""
    ext = Path(file.filename or '').suffix.lower()
    if ext in _ALLOWED_EXTENSIONS:
        return ext
    # No recognised extension — try content-type
    ct = (file.content_type or '').split(';')[0].strip().lower()
    return _CONTENT_TYPE_MAP.get(ct, ext)


@router.post("/documents")
async def upload_documents(files: List[UploadFile] = File(...)):
    """
    Upload health documents (PDF, images, CSV) for processing.

    Supported formats: PDF, JPG, JPEG, PNG, HEIC, HEIF, CSV
    """
    try:
        uploaded_files = []

        for file in files:
            file_ext = _resolve_extension(file)

            if file_ext not in _ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"File type '{file_ext or 'unknown'}' is not supported. "
                        f"Allowed: {sorted(_ALLOWED_EXTENSIONS)}"
                    ),
                )

            # Generate unique filename with correct extension
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = Path(file.filename or 'document').stem
            safe_filename = f"{timestamp}_{stem}{file_ext}"
            file_path = settings.RAW_DATA_DIR / safe_filename

            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            logger.info('Saved upload: %s (%s)', safe_filename, file_ext)

            if file_ext == '.csv':
                health_data = csv_importer.import_from_csv(file_path)
                text_content = f"CSV import: {len(health_data)} records"
            else:
                text_content = doc_processor.process_document(file_path)
                health_data = data_extractor.extract_health_metrics(text_content)

            uploaded_files.append({
                "filename": safe_filename,
                "original_name": file.filename,
                "path": str(file_path),
                "extracted_text_length": len(text_content),
                "health_metrics_found": len(health_data),
            })

        return {
            "message": f"Successfully uploaded {len(uploaded_files)} files",
            "files": uploaded_files,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Upload failed: %s', e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/csv-template")
async def download_csv_template():
    """Stiahne vzorový CSV súbor pre manuálny import dát"""
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
    """List all uploaded documents"""
    try:
        files = list(settings.RAW_DATA_DIR.glob("*"))
        return {
            "count": len(files),
            "files": [
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "created": f.stat().st_ctime,
                }
                for f in files if f.is_file()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
