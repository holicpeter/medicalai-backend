import io
import json
import logging
import os
import re
from typing import Dict, List, Optional

import anthropic
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'
# Malý/lacný model zámerne — ide o úzku, vysokofrekvenčnú extrakčnú úlohu
# (3-5x/deň/používateľ), nie o hlboké uvažovanie. Pozri projektový dokument
# ai-model-selection-strategy.md pre zdôvodnenie tohto výberu.

_ANALYSIS_INSTRUCTION = """\
You are analyzing a photo of a meal for a health-tracking app. Identify every
distinct food item visible, estimate its portion size in grams using visual
cues (plate size, cutlery, common serving sizes), and estimate its calories
and macronutrients at that estimated portion.

Be honest that portion estimation from a single photo has real limits,
especially for mixed dishes with hidden ingredients (oil, sauces, sugar) —
reflect that in the confidence values rather than always returning a high
number.

Return ONLY a JSON object — no other text, no markdown, no explanation.

Format:
{
  "items": [
    {"name": "grilované kuracie prsia", "estimated_grams": 150, "calories": 248, "protein_g": 46.5, "carbs_g": 0, "fat_g": 5.4, "confidence": 0.6},
    {"name": "ryža", "estimated_grams": 180, "calories": 234, "protein_g": 4.9, "carbs_g": 50.6, "fat_g": 0.4, "confidence": 0.5}
  ],
  "total_calories": 482,
  "total_protein_g": 51.4,
  "total_carbs_g": 50.6,
  "total_fat_g": 5.8,
  "overall_confidence": 0.55,
  "recommendation": "Jedna až dve krátke vety po slovensky, praktické a neklinické, NIE diagnóza."
}

Rules:
- estimated_grams, calories, protein_g, carbs_g, fat_g must be numbers >= 0
- confidence and overall_confidence are numbers between 0.0 and 1.0
- name should be in Slovak
- recommendation must be in Slovak
- include every distinct food item you can identify, even small ones
"""


def _read_image_as_jpeg(image_bytes: bytes) -> bytes:
    """Normalise any supported input (incl. HEIC, via the app-wide heif opener
    registered in app.ocr.document_processor) to a JPEG Claude will accept."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


class MealAnalyzer:
    """Claude vision integrácia pre analýzu jedla z fotky."""

    def __init__(self):
        from app.config import settings
        api_key = settings.ANTHROPIC_API_KEY or os.environ.get('ANTHROPIC_API_KEY')
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def analyze_meal_photo(self, image_bytes: bytes, media_type: str) -> Dict:
        """Zavolá Claude vision na fotke jedla a vráti spracovaný (validovaný) dict.

        Vyhadzuje RuntimeError, ak klient nie je nakonfigurovaný, alebo
        ValueError, ak sa z odpovede nedá vyparsovať platný výsledok.
        """
        if self.client is None:
            raise RuntimeError('ANTHROPIC_API_KEY is not set')

        if media_type not in ('image/jpeg', 'image/png'):
            # HEIC a pod. prevedieme na JPEG (rovnaký prístup ako pri OCR skenoch).
            image_bytes = _read_image_as_jpeg(image_bytes)
            media_type = 'image/jpeg'

        import base64
        file_data = base64.standard_b64encode(image_bytes).decode('utf-8')

        content = [
            {
                'type': 'image',
                'source': {'type': 'base64', 'media_type': media_type, 'data': file_data},
            },
            {
                'type': 'text',
                'text': _ANALYSIS_INSTRUCTION,
                'cache_control': {'type': 'ephemeral'},
            },
        ]

        message = self.client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1024,
            messages=[{'role': 'user', 'content': content}],
        )

        text = message.content[0].text
        logger.info('Meal analysis response: %d characters', len(text))
        return parse_meal_analysis(text)


def parse_meal_analysis(text: str) -> Dict:
    """Parsuje JSON objekt z odpovede Claude, s rovnakým prístupom ako
    HealthDataExtractor._parse_json — strip markdown fences, nájdi {...},
    validuj a znormalizuj polia."""
    stripped = text.strip()
    stripped = re.sub(r'^```(?:json)?\s*', '', stripped)
    stripped = re.sub(r'\s*```$', '', stripped)
    start = stripped.find('{')
    end = stripped.rfind('}') + 1
    if start == -1 or end <= start:
        raise ValueError('No JSON object found in model response')

    raw = json.loads(stripped[start:end])

    raw_items = raw.get('items') if isinstance(raw, dict) else None
    items: List[Dict] = []
    for item in (raw_items or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        items.append({
            'name': name,
            'estimated_grams': _to_number(item.get('estimated_grams')),
            'calories': _to_number(item.get('calories')),
            'protein_g': _to_number(item.get('protein_g')),
            'carbs_g': _to_number(item.get('carbs_g')),
            'fat_g': _to_number(item.get('fat_g')),
            'confidence': _clamp01(_to_number(item.get('confidence'), default=0.5)),
        })

    if items:
        total_calories = round(sum(i['calories'] for i in items), 1)
        total_protein_g = round(sum(i['protein_g'] for i in items), 1)
        total_carbs_g = round(sum(i['carbs_g'] for i in items), 1)
        total_fat_g = round(sum(i['fat_g'] for i in items), 1)
    else:
        total_calories = _to_number(raw.get('total_calories'))
        total_protein_g = _to_number(raw.get('total_protein_g'))
        total_carbs_g = _to_number(raw.get('total_carbs_g'))
        total_fat_g = _to_number(raw.get('total_fat_g'))

    recommendation = raw.get('recommendation')
    if not isinstance(recommendation, str) or not recommendation.strip():
        recommendation = 'Skús udržať vyváženú porciu bielkovín, sacharidov a tukov pri ďalšom jedle.'

    return {
        'items': items,
        'total_calories': total_calories,
        'total_protein_g': total_protein_g,
        'total_carbs_g': total_carbs_g,
        'total_fat_g': total_fat_g,
        'overall_confidence': _clamp01(_to_number(raw.get('overall_confidence'), default=0.5)),
        'recommendation': recommendation,
    }


def _to_number(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        n = float(str(value).replace(',', '.'))
    except (ValueError, TypeError):
        return default
    return round(n, 1) if n >= 0 else default


def _clamp01(value: Optional[float]) -> float:
    if value is None:
        return 0.5
    return max(0.0, min(1.0, value))
