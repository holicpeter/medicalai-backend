import base64
import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple, Union

import anthropic
from PIL import Image

try:  # pypdf is the maintained successor; PyPDF2 3.x exposes the same names
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover - depends on which is installed
    from PyPDF2 import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

_HEIC_EXTENSIONS = {'.heic', '.heif'}

# A whole scanned health card does not fit in one request. The Messages API
# caps a request at 32 MB — and base64 inflates a PDF by a third, so a 33 MB
# scan arrives as ~44 MB and is refused with 413 request_too_large — and at 100
# pages per request, which a full card reaches on its own. Pages are therefore
# sent in batches: small enough that neither limit is anywhere near, and small
# enough that the reply fits in max_tokens.
MAX_PAGES_PER_BATCH = 10
MAX_BATCH_BYTES = 12 * 1024 * 1024

# Batches run concurrently, or a hundred-page card would be a dozen sequential
# round trips and the upload would sit there for minutes.
MAX_PARALLEL_BATCHES = 3

RECORD_MARKER = '=== ZÁZNAM ==='
METRICS_MARKER = '=== METRIKY ==='

_EXTRACTION_INSTRUCTION = f"""\
Extract information from this Slovak medical document in TWO sections, in this
exact order and with these exact markers.

{RECORD_MARKER}
Everything that is NOT a measured value, in Slovak: diagnoses, operations and
procedures with their dates, hospitalisations, medication, allergies, doctors'
conclusions and recommendations, referrals. Keep dates, names of procedures and
medication exactly as written. Be complete — this is the only record of the
document's text. If a page holds nothing but a table of laboratory values,
write a single dash for it.

{METRICS_MARKER}
A JSON array of the lab values and metrics — no markdown, no explanation.

Format:
[
  {{"metric": "glucose", "value": 5.2, "unit": "mmol/l", "date": "2022-08-04", "status": "OK"}},
  {{"metric": "cholesterol", "value": 4.8, "unit": "mmol/l", "date": "2022-08-04", "status": "HIGH"}}
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


def split_document_output(text: str) -> Tuple[str, str]:
    """Separate the written record from the metrics JSON.

    The extractor takes everything between the first '[' and the last ']', so
    the record has to be cut away before it reaches it — a reference range
    written as [3.5-5.5] in a report would otherwise swallow the real array.
    Output without the marker is treated as metrics only, which is what older
    documents and a model that ignores the format both produce.
    """
    if METRICS_MARKER not in text:
        return '', text

    record, _, metrics = text.partition(METRICS_MARKER)
    record = record.replace(RECORD_MARKER, '').strip()
    if record in {'-', '—', '–'}:
        record = ''
    return record, metrics.strip()


def _pdf_batches(file_path: Path) -> List[bytes]:
    """Split a PDF into page batches that fit comfortably in one request."""
    reader = PdfReader(str(file_path))
    page_count = len(reader.pages)
    if page_count == 0:
        raise ValueError(f'{file_path.name} has no pages')

    total_bytes = file_path.stat().st_size
    per_page = max(1, total_bytes // page_count)
    pages_per_batch = max(1, min(MAX_PAGES_PER_BATCH, MAX_BATCH_BYTES // per_page))

    if page_count <= pages_per_batch and total_bytes <= MAX_BATCH_BYTES:
        with open(file_path, 'rb') as f:
            return [f.read()]

    batches: List[bytes] = []
    for start in range(0, page_count, pages_per_batch):
        writer = PdfWriter()
        for page in reader.pages[start:start + pages_per_batch]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        batches.append(buf.getvalue())

    logger.info(
        'Split %s into %d batches of up to %d pages (%d pages, %.1f MB)',
        file_path.name, len(batches), pages_per_batch, page_count,
        total_bytes / (1024 * 1024),
    )
    return batches


def _merge_outputs(outputs: List[str]) -> str:
    """Fold per-batch replies back into one document-shaped reply.

    Metrics are merged as data rather than by pasting arrays together, so one
    batch the model answered in an unexpected shape cannot corrupt the array
    for all the others; its raw reply is kept in the record instead of being
    dropped on the floor.
    """
    records: List[str] = []
    metrics: List[dict] = []

    for index, output in enumerate(outputs, start=1):
        record, metrics_text = split_document_output(output)
        if record:
            records.append(record)

        start, end = metrics_text.find('['), metrics_text.rfind(']') + 1
        parsed = None
        if start != -1 and end > start:
            try:
                parsed = json.loads(metrics_text[start:end])
            except json.JSONDecodeError as e:
                logger.warning('Batch %d: metrics are not valid JSON: %s', index, e)

        if isinstance(parsed, list):
            metrics.extend(parsed)
        elif metrics_text.strip():
            logger.warning('Batch %d: keeping unparsed reply in the record', index)
            records.append(metrics_text.strip())

    return (
        f'{RECORD_MARKER}\n'
        + '\n\n'.join(records)
        + f'\n\n{METRICS_MARKER}\n'
        + json.dumps(metrics, ensure_ascii=False)
    )


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
            batches = _pdf_batches(file_path)
            if len(batches) == 1:
                text = self._ask(self._pdf_content(batches[0]))
                logger.info('Extracted %d characters from %s', len(text), file_path.name)
                return text

            with ThreadPoolExecutor(max_workers=MAX_PARALLEL_BATCHES) as pool:
                outputs = list(pool.map(
                    lambda data: self._ask(self._pdf_content(data)), batches
                ))

            text = _merge_outputs(outputs)
            logger.info(
                'Extracted %d characters from %s across %d batches',
                len(text), file_path.name, len(batches),
            )
            return text
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

        text = self._ask(content)
        logger.info('Extracted %d characters from %s', len(text), file_path.name)
        return text

    @staticmethod
    def _pdf_content(pdf_bytes: bytes) -> list:
        return [
            {
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': 'application/pdf',
                    'data': base64.standard_b64encode(pdf_bytes).decode('utf-8'),
                },
            },
            {
                'type': 'text',
                'text': _EXTRACTION_INSTRUCTION,
                'cache_control': {'type': 'ephemeral'},
            },
        ]

    def _ask(self, content: list) -> str:
        message = self.client.messages.create(
            model='claude-opus-4-5',
            # The reply now carries the document's written record as well as the
            # metrics, so 4096 is no longer enough for a batch of pages.
            max_tokens=8192,
            messages=[{'role': 'user', 'content': content}],
        )
        return message.content[0].text
