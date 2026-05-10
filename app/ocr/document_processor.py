from pathlib import Path
from typing import Union
import base64
import os
import httpx
import json


class DocumentProcessor:
    def __init__(self):
        self.api_key = os.environ.get('MISTRAL_API_KEY')

    def process_document(self, file_path: Union[str, Path]) -> str:
        file_path = Path(file_path)
        print(f'[OCR] Processing: {file_path.name}')

        with open(file_path, 'rb') as f:
            file_data = base64.standard_b64encode(f.read()).decode('utf-8')

        if file_path.suffix.lower() == '.pdf':
            media_type = 'application/pdf'
        elif file_path.suffix.lower() in ['.jpg', '.jpeg']:
            media_type = 'image/jpeg'
        else:
            media_type = 'image/png'

        payload = {
            'model': 'pixtral-12b-2409',
            'messages': [{
                'role': 'user',
                'content': [
                    {
                        'type': 'image_url',
                        'image_url': {'url': f'data:{media_type};base64,{file_data}'}
                    },
                    {
                        'type': 'text',
                        'text': 'Extract ALL health data from this Slovak medical document. Include every lab value, blood test result, urine test result, date, doctor name, diagnosis, medication.'
                    }
                ]
            }]
        }

        response = httpx.post(
            'https://api.mistral.ai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=120.0
        )

        result = response.json()
        text = result['choices'][0]['message']['content']
        print(f'[OCR] Extracted {len(text)} characters')
        return text
