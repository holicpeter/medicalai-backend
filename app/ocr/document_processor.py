from pathlib import Path
from typing import Union
import base64
import os

class DocumentProcessor:
    def __init__(self):
        self.api_key = os.environ.get("MISTRAL_API_KEY")
    
    def process_document(self, file_path: Union[str, Path]) -> str:
        from mistralai import Mistral
        file_path = Path(file_path)
        print(f"[OCR] Processing: {file_path.name}")
        
        client = Mistral(api_key=self.api_key)
        
        with open(file_path, "rb") as f:
            file_data = base64.standard_b64encode(f.read()).decode("utf-8")
        
        if file_path.suffix.lower() == ".pdf":
            media_type = "application/pdf"
        elif file_path.suffix.lower() in [".jpg", ".jpeg"]:
            media_type = "image/jpeg"
        else:
            media_type = "image/png"
        
        response = client.chat.complete(
            model="pixtral-12b-2409",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{file_data}"}
                    },
                    {
                        "type": "text",
                        "text": "Extract ALL health data from this Slovak medical document. Include every lab value, blood test result, urine test result, date, doctor name, diagnosis, medication. Be very thorough and extract every number and measurement you see."
                    }
                ]
            }]
        )
        
        text = response.choices[0].message.content
        print(f"[OCR] Extracted {len(text)} characters")
        return text
