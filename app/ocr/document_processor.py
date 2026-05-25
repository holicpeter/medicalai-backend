import io
import logging
import os
from pathlib import Path
from typing import Union
import base64

import anthropic
from PIL import Image

logger = logging.getLogger(__name__)

_HEIC_EXTENSIONS = {'.heic', '.heif'}

_EXTRACTION_INSTRUCTION = """\
Extract ALL lab values and medical metrics from this Slovak medical document.
Return ONLY a JSON array — no other text, no markdown, no explanation.

Format:
[
  {"metric": "glucose", "value": 5.2, "unit": "mmol/l", "date": "2022-08-04", "status": "OK"},
  {"metric": "cholesterol", "value": 4.8, "unit": "mmol/l", "date": "2022-08-04", "status": "HIGH"}
]

Use these standard metric names:
- glucose (glukóza, glykémia, S_Glukóza)
- cholesterol (S_Cholesterol)
- ldl (LDL, S_LDL-chol)
- hdl (HDL, S_HDL-chol)
- triglycerides (triglyceridy, S_Triacylglyceroly)
- hba1c (HbA1c)
- creatinine (kreatinín, S_Kreatinín)
- alt (ALT, ALAT, S_ALT)
- ast (AST, ASAT, S_AST)
- ggt (GGT, S_GGT)
- hemoglobin (hemoglobín, B_Hemoglobin HGB)
- leukocytes (leukocyty, B_Leukocyty WBC)
- platelets (trombocyty, B_Trombocyty PLT)
- erythrocytes (erytrocyty, B_Erytrocyty RBC)
- crp (CRP, S_CRP)
- urea (urea, S_Urea)
- uric_acid (kyselina močová, S_Kyselina močová)
- tsh (TSH)
- bmi (BMI)
- weight (hmotnosť, váha)
- blood_pressure (krvný tlak, format value as "120/80")

Rules:
- value must be a number (or "systolic/diastolic" string for blood_pressure)
- date format: YYYY-MM-DD (extract from document header/footer)
- status: "OK", "HIGH", "LOW", or "ABNORMAL"
- include EVERY numeric lab value found
- if date not found, omit the date field
"""


def _load_heif_support():
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        logger.warning('pillow-heif not installed — HEIC/HEIF files cannot be processed')


_load_heif_support()


def _read_image_as_jpeg(file_path: Path) -> bytes:
    img = Image.open(file_path)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


class DocumentProcessor:
    def __init__(self):
        from app.config import settings
        api_key = settings.ANTHROPIC_API_KEY or os.environ.get('ANTHROPIC_API_KEY')
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def process_document(self, file_path: Union[str, Path]) -> str:
        if self.client is None:
            raise RuntimeError('ANTHROPIC_API_KEY is not set')

        file_path = Path(file_path)
        suffix = file_path.suffix.lower()
        logger.info('Processing document: %s', file_path.name)

        if suffix == '.pdf':
            with open(file_path, 'rb') as f:
                file_data = base64.standard_b64encode(f.read()).decode('utf-8')
            content = [
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': file_data,
                    },
                },
                {
                    'type': 'text',
                    'text': _EXTRACTION_INSTRUCTION,
                    'cache_control': {'type': 'ephemeral'},
                },
            ]
        else:
            if suffix in _HEIC_EXTENSIONS:
                logger.info('Converting HEIC/HEIF to JPEG: %s', file_path.name)
                image_bytes = _read_image_as_jpeg(file_path)
                media_type = 'image/jpeg'
            else:
                with open(file_path, 'rb') as f:
                    image_bytes = f.read()
                media_type = 'image/jpeg' if suffix in {'.jpg', '.jpeg'} else 'image/png'

            file_data = base64.standard_b64encode(image_bytes).decode('utf-8')
            content = [
                {
                    'type': 'image',
                    'source': {'type': 'base64', 'media_type': media_type, 'data': file_data},
                },
                {
                    'type': 'text',
                    'text': _EXTRACTION_INSTRUCTION,
                    'cache_control': {'type': 'ephemeral'},
                },
            ]

        message = self.client.messages.create(
            model='claude-opus-4-5',
            max_tokens=4096,
            messages=[{'role': 'user', 'content': content}],
        )

        text = message.content[0].text
        logger.info('Extracted %d characters from %s', len(text), file_path.name)
        return text
