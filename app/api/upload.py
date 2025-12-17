from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List
import shutil
from pathlib import Path
from datetime import datetime

from app.config import settings
from app.ocr.document_processor import DocumentProcessor
from app.ocr.data_extractor import HealthDataExtractor
from app.ocr.csv_importer import CSVImporter

router = APIRouter()
doc_processor = DocumentProcessor()
data_extractor = HealthDataExtractor()
csv_importer = CSVImporter()

@router.post("/documents")
async def upload_documents(files: List[UploadFile] = File(...)):
    """
    Upload health documents (PDF, images, CSV) for processing
    
    Supported formats:
    - PDF: Scanned or digital health records
    - Images: JPG, JPEG, PNG (photos of results)
    - CSV: Manual data entry (see /csv-template for format)
    """
    try:
        uploaded_files = []
        
        for file in files:
            # Validate file type
            allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png', '.csv'}
            file_ext = Path(file.filename).suffix.lower()
            
            if file_ext not in allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"File type {file_ext} not allowed. Allowed: {allowed_extensions}"
                )
            
            # Generate unique filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"{timestamp}_{file.filename}"
            file_path = settings.RAW_DATA_DIR / safe_filename
            
            # Save file
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Process based on file type
            if file_ext == '.csv':
                # CSV import - direct data entry
                health_data = csv_importer.import_from_csv(file_path)
                text_content = f"CSV import: {len(health_data)} records"
            else:
                # OCR processing for PDF/images
                text_content = doc_processor.process_document(file_path)
                
                # Extract health data
                health_data = data_extractor.extract_health_metrics(text_content)
            
            uploaded_files.append({
                "filename": safe_filename,
                "original_name": file.filename,
                "path": str(file_path),
                "extracted_text_length": len(text_content),
                "health_metrics_found": len(health_data)
            })
        
        return {
            "message": f"Successfully uploaded {len(uploaded_files)} files",
            "files": uploaded_files
        }
    
    except Exception as e:
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
            "instructions": "Fill in your health data and upload via /api/upload/documents"
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
                    "created": f.stat().st_ctime
                }
                for f in files if f.is_file()
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
