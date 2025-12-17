from pathlib import Path
from typing import Union
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
import PyPDF2

from app.config import settings

class DocumentProcessor:
    """Spracovanie zdravotných dokumentov (PDF, obrázky) pomocou OCR"""
    
    def __init__(self):
        self.tesseract_lang = settings.TESSERACT_LANG
    
    def process_document(self, file_path: Union[str, Path]) -> str:
        """
        Spracuje dokument a extrahuje text
        
        Args:
            file_path: Cesta k dokumentu
            
        Returns:
            Extrahovaný text z dokumentu
        """
        file_path = Path(file_path)
        
        if file_path.suffix.lower() == '.pdf':
            return self._process_pdf(file_path)
        elif file_path.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            return self._process_image(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")
    
    def _process_pdf(self, pdf_path: Path) -> str:
        """Spracuje PDF dokument"""
        text_content = []
        
        try:
            print(f"[OCR] Starting PDF processing: {pdf_path.name}")
            # Pokus o extrahovanie textu priamo z PDF
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                print(f"[OCR] PDF has {len(pdf_reader.pages)} pages")
                for page_num, page in enumerate(pdf_reader.pages, 1):
                    print(f"[OCR] Extracting text from page {page_num}/{len(pdf_reader.pages)}")
                    page_text = page.extract_text()
                    if page_text.strip():
                        text_content.append(page_text)
            
            # Ak sa nepodarilo extrahovať text, použiť OCR
            if not text_content or len(''.join(text_content).strip()) < 100:
                print(f"[OCR] Not enough text extracted ({len(''.join(text_content))} chars), switching to OCR...")
                text_content = self._ocr_pdf(pdf_path)
            else:
                print(f"[OCR] Text extraction successful: {len(''.join(text_content))} characters")
        
        except Exception as e:
            print(f"Error processing PDF with PyPDF2: {e}")
            # Fallback na OCR
            text_content = self._ocr_pdf(pdf_path)
        
        return '\n\n'.join(text_content)
    
    def _ocr_pdf(self, pdf_path: Path) -> list:
        """OCR pre PDF (konvertuje na obrázky a spracuje)"""
        text_content = []
        
        try:
            # Konvertovať PDF na obrázky
            print(f"[OCR] Converting PDF to images with Poppler...")
            try:
                images = convert_from_path(str(pdf_path))
                print(f"[OCR] Successfully converted PDF to {len(images)} images")
            except Exception as poppler_error:
                print(f"[OCR ERROR] Poppler failed: {poppler_error}")
                print(f"Warning: Poppler not installed. Fallback to basic extraction.")
                print(f"Install Poppler from: https://github.com/oschwartz10612/poppler-windows/releases")
                # Fallback: použiť len PyPDF2 bez OCR
                return [f"PDF obsahuje {self._get_pdf_page_count(pdf_path)} strán. Pre OCR nainštalujte Poppler."]
            
            for i, image in enumerate(images):
                print(f"[OCR] Processing page {i+1}/{len(images)}...")
                text = self._ocr_image(image)
                print(f"[OCR] Page {i+1} extracted {len(text)} characters")
                text_content.append(text)
        
        except Exception as e:
            print(f"[OCR ERROR] Error during OCR of PDF: {e}")
            raise
        
        return text_content
    
    def _get_pdf_page_count(self, pdf_path: Path) -> int:
        """Získa počet strán v PDF"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                return len(pdf_reader.pages)
        except:
            return 0
    
    def _process_image(self, image_path: Path) -> str:
        """Spracuje obrázok pomocou OCR"""
        try:
            image = Image.open(image_path)
            return self._ocr_image(image)
        except Exception as e:
            print(f"Error processing image: {e}")
            raise
    
    def _ocr_image(self, image: Image.Image) -> str:
        """Vykoná OCR na obrázku s vylepšením pre rukopis"""
        try:
            # Pre-processing obrázka pre lepšiu OCR kvalitu
            enhanced_image = self._enhance_image_for_ocr(image)
            
            # Použiť slovenčinu + angličtinu pre lepšie výsledky
            # PSM 3 = Fully automatic page segmentation (lepšie pre zmiešaný obsah)
            text = pytesseract.image_to_string(
                enhanced_image,
                lang=f'{self.tesseract_lang}+eng',
                config='--psm 3 --oem 3'  # PSM 3 = auto, OEM 3 = default (najlepší)
            )
            
            # Ak je text prázdny alebo veľmi krátky, skús agresívnejšie nastavenia
            if len(text.strip()) < 50:
                print("Trying alternative OCR settings for poor quality...")
                text = pytesseract.image_to_string(
                    enhanced_image,
                    lang=f'{self.tesseract_lang}+eng',
                    config='--psm 6 --oem 1'  # Uniformný blok, legacy engine
                )
            
            return text
        except Exception as e:
            print(f"OCR error: {e}")
            # Fallback len na angličtinu
            return pytesseract.image_to_string(image, lang='eng')
    
    def _enhance_image_for_ocr(self, image: Image.Image) -> Image.Image:
        """Vylepší obrázok pre lepšie OCR výsledky"""
        try:
            from PIL import ImageEnhance, ImageFilter
            
            # Konvertovať na grayscale
            if image.mode != 'L':
                image = image.convert('L')
            
            # Zväčšiť rozlíšenie ak je príliš malé
            width, height = image.size
            if width < 2000:
                scale_factor = 2000 / width
                new_size = (int(width * scale_factor), int(height * scale_factor))
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            # Zvýšiť kontrast pre lepšie rozlíšenie textu
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)
            
            # Zvýšiť ostrosť
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(1.5)
            
            # Odstránenie šumu (median filter)
            image = image.filter(ImageFilter.MedianFilter(size=3))
            
            return image
        except Exception as e:
            print(f"Image enhancement error: {e}")
            # Vrátiť pôvodný obrázok ak preprocessing zlyhal
            return image
