from pathlib import Path
from typing import Union
import base64
import os
import anthropic

class DocumentProcessor:
    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )
    
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
        
        message = self.client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document" if media_type == "application/pdf" else "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": file_data
                        }
                    },
                    {
                        "type": "text",
                        "text": "Extract all text and health data from this medical document. Include all values, dates, and measurements."
                    }
                ]
            }]
        )
        
        text = message.content[0].text
        print(f"[OCR] Extracted {len(text)} characters")
        return text
