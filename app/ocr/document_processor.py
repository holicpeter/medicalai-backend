from pathlib import Path
from typing import Union
import base64
import os
from mistralai.client import MistralClient

class DocumentProcessor:
    def __init__(self):
        self.client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY"))
    
    def process_document(self, file_path: Union[str, Path]) -> str:
        file_path = Path(file_path)
        print(f"[OCR] Processing: {file_path.name}")
        
        with open(file_path, "rb") as f:
            file_data = base64.standard_b64encode(f.read()).decode("utf-8")
        
        if file_path.suffix.lower() == ".pdf":
            media_type = "application/pdf"
        elif file_path.suffix.lower() in [".jpg", ".jpeg"]:
            media_type = "image/jpeg"
        else:
            media_type = "image/png"
        
        response = self.client.chat.complete(
            model="mistral-small-latest",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": f"data:{media_type};base64,{file_data}"
                    },
                    {
                        "type": "text",
                        "text": "Extract all text and health data from this medical document. Include all values, dates, and measurements."
                    }
                ]
            }]
        )
        
        text = response.choices[0].message.content
        print(f"[OCR] Extracted {len(text)} characters")
        return text
