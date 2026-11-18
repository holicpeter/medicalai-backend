"""Fotka posilňovacieho stroja -> Claude vision -> ktorý cvik sa na ňom robí.

Rovnaký vzor ako app/nutrition/analyzer.py: malý lacný model (úzka
rozpoznávacia úloha), inštrukcia v cachovanom bloku, premenlivé časti
(zoznam cvikov z histórie používateľa) až za cache breakpointom.
"""
import base64
import json
import logging
import os
import re
from typing import Dict, List, Optional

import anthropic

from app.i18n import current_lang
from app.nutrition.analyzer import _read_image_as_jpeg

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

_INSTRUCTION = """\
You are looking at a photo taken in a gym by a user of a fitness and health
tracking app. Identify the exercise machine or equipment and the exercise
it is used for.

Return ONLY a JSON object — no other text, no markdown.

Format:
{
  "equipment": "short name of the machine or equipment",
  "exercise": "name of the main exercise done on it",
  "category": "strength",
  "muscle_groups": ["main muscle group", "secondary muscle group"],
  "how_to": "two or three short sentences on correct, safe technique",
  "alternatives": ["other exercise that can be done on the same equipment"],
  "confidence": 0.8
}

Rules:
- category is one of: strength, cardio, other
- confidence is a number between 0.0 and 1.0; be honest when the photo is
  unclear or the equipment could be several things
- if the photo shows no exercise equipment at all, return exercise "" and
  confidence 0
- if the user's existing exercises (listed after this instruction) contain
  the exercise you identified, use exactly that name for "exercise", so the
  new workout joins the user's history
- all text values must be in {language}
- the technique tip is general fitness guidance, not medical advice
"""

_LANGUAGE = {"sk": "Slovak", "en": "English"}

_CATEGORIES = {'strength', 'cardio', 'other'}


def _instruction() -> str:
    return _INSTRUCTION.replace('{language}', _LANGUAGE.get(current_lang(), 'Slovak'))


class MachineIdentifier:
    def __init__(self):
        from app.config import settings
        api_key = settings.ANTHROPIC_API_KEY or os.environ.get('ANTHROPIC_API_KEY')
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def identify(self, image_bytes: bytes, media_type: str, known_exercises: List[str]) -> Dict:
        if self.client is None:
            raise RuntimeError('ANTHROPIC_API_KEY is not set')
        if media_type not in ('image/jpeg', 'image/png'):
            image_bytes = _read_image_as_jpeg(image_bytes)
            media_type = 'image/jpeg'

        known = '\n'.join(f'- {name}' for name in known_exercises[:100]) or '(none yet)'
        content = [
            {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': media_type,
                    'data': base64.standard_b64encode(image_bytes).decode('utf-8'),
                },
            },
            {'type': 'text', 'text': _instruction(), 'cache_control': {'type': 'ephemeral'}},
            {'type': 'text', 'text': f"The user's existing exercises:\n{known}"},
        ]
        message = self.client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=700,
            messages=[{'role': 'user', 'content': content}],
        )
        return parse_identification(message.content[0].text)


def _strings(value, limit: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip()[:100] for v in value if str(v).strip()][:limit]


def parse_identification(text: str) -> Dict:
    stripped = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    start, end = stripped.find('{'), stripped.rfind('}') + 1
    if start == -1 or end <= start:
        raise ValueError('No JSON object found in model response')
    raw = json.loads(stripped[start:end])
    if not isinstance(raw, dict):
        raise ValueError('Model response is not an object')

    category = str(raw.get('category') or 'strength').strip().lower()
    try:
        confidence: Optional[float] = float(raw.get('confidence'))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        'equipment': str(raw.get('equipment') or '').strip()[:200],
        'exercise': str(raw.get('exercise') or '').strip()[:200],
        'category': category if category in _CATEGORIES else 'strength',
        'muscle_groups': _strings(raw.get('muscle_groups'), 6),
        'how_to': str(raw.get('how_to') or '').strip()[:1000],
        'alternatives': _strings(raw.get('alternatives'), 5),
        'confidence': max(0.0, min(1.0, confidence)),
    }
