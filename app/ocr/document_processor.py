import logging
import os
from pathlib import Path
from typing import Union
import base64

import anthropic

logger = logging.getLogger(__name__)

_EXTRACTION_INSTRUCTION = (
    'Extract ALL health data from this Slovak medical document. '
    'Include every lab value, blood test result, urine test result, '
    'date, doctor name, diagnosis, medication.'
)


class DocumentProcessor:
    def __init__(self):
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def process_document(self, file_path: Union[str, Path]) -> str:
        if self.client is None:
            raise RuntimeError('ANTHROPIC_API_KEY is not set')

        file_path = Path(file_path)
        logger.info('Processing document: %s', file_path.name)

        with open(file_path, 'rb') as f:
            file_data = base64.standard_b64encode(f.read()).decode('utf-8')

        if file_path.suffix.lower() == '.pdf':
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
            media_type = (
                'image/jpeg'
                if file_path.suffix.lower() in {'.jpg', '.jpeg'}
                else 'image/png'
            )
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
