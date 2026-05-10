from pathlib import Path
from typing import Union
import base64
import os
import httpx


class DocumentProcessor:
    def __init__(self):
        self.api_key = os.environ.get('ANTHROPIC_API_KEY')

    def process_document(self, file_path: Union[str, Path]) -> str:
        file_path = Path(file_path)
        print(f'[OCR] Processing: {file_path.name}')

        with open(file_path, 'rb') as f:
            file_data = base64.standard_b64encode(f.read()).decode('utf-8')

        if file_path.suffix.lower() == '.pdf':
            content = [
                {
                    'type': 'document',
                    'source': {
                        'type': 'base64',
                        'media_type': 'application/pdf',
                        'data': file_data
                    }
                },
                {
                    'type': 'text',
                    'text': 'Extract ALL health data from this Slovak medical document. Include every lab value, blood test result, urine test result, date, doctor name, diagnosis, medication.'
                }
            ]
        else:
            media_type = 'image/jpeg' if file_path.suffix.lower() in ['.jpg', '.jpeg'] else 'image/png'
            content = [
                {
                    'type': 'image',
                    'source': {'type': 'base64', 'media_type': media_type, 'data': file_data}
                },
                {
                    'type': 'text',
                    'text': 'Extract ALL health data from this Slovak medical document.'
                }
            ]

        response = httpx.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': self.api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'claude-opus-4-5',
                'max_tokens': 4096,
                'messages': [{'role': 'user', 'content': content}]
            },
            timeout=120.0
        )

        response.raise_for_status()
        result = response.json()
        text = result.get('content', [{}])[0].get('text', '')
        print(f'[OCR] Extracted {len(text)} characters')
        return text
