import re
from datetime import datetime
from typing import Dict, List, Optional
import json
from pathlib import Path

from app.config import settings

class HealthDataExtractor:
    """Extrahuje zdravotné metriky z textu"""
    
    def __init__(self):
        self.patterns = self._init_patterns()
    
    def _init_patterns(self) -> Dict:
        """Inicializuje regex vzory pre extrakciu dát"""
        return {
            # Krvný tlak (systolic/diastolic)
            'blood_pressure': [
                r'(?:tlak|krvný tlak|TK|BP|RR)[\s:]*(\d{2,3})\s*/\s*(\d{2,3})',
                r'(\d{2,3})\s*/\s*(\d{2,3})\s*(?:mmHg|mm Hg)',
            ],
            
            # Glukóza
            'glucose': [
                r'(?:glukóza|glukoza|glykémia|glykemia|cukор|glucose|GLU|Glu|glu)[\s:]*(\d+[.,]?\d*)',
                r'(?:S-GLU|S<GLU)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # HbA1c
            'hba1c': [
                r'HbA1c[\s:]*(\d+[.,]?\d*)',
                r'glykovaný hemoglobín[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Cholesterol
            'cholesterol': [
                r'(?:cholesterol|CHOL|Chol|chol)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # LDL
            'ldl': [
                r'LDL[\s:]*(\d+[.,]?\d*)',
            ],
            
            # HDL
            'hdl': [
                r'HDL[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Triglyceridy
            'triglycerides': [
                r'(?:triglyceridy|trigliceridy|TG|TAG|Trig)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Kreatinín
            'creatinine': [
                r'(?:kreatinin|creatinine|KREAT|Krea|Kreat)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Bilirubín
            'bilirubin': [
                r'(?:bilirubin|BIL|Bil)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # ALT/AST (pečeňové enzýmy)
            'alt': [
                r'(?:ALT|ALAT|SGPT)[\s:]*(\d+[.,]?\d*)',
            ],
            
            'ast': [
                r'(?:AST|ASAT|SGOT)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Hemoglobín
            'hemoglobin': [
                r'(?:hemoglobin|haemoglobin|Hb|HGB)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Erytrocyty
            'erythrocytes': [
                r'(?:erytrocyty|RBC|Er|červené krvinky)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Leukocyty
            'leukocytes': [
                r'(?:leukocyty|WBC|Le|biele krvinky)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Trombocyty
            'platelets': [
                r'(?:trombocyty|PLT|Tr|krvné doštičky)[\s:]*(\d+[.,]?\d*)',
            ],
            
            # BMI
            'bmi': [
                r'BMI[\s:]*(\d+[.,]?\d*)',
            ],
            
            # Hmotnosť
            'weight': [
                r'(?:hmotnosť|hmotnost|váha|vaha|weight|Hmot\.|Vaha|Véha)[\s:]*(\d+[.,]?\d*)\s*kg',
            ],
            
            # Výška
            'height': [
                r'(?:výška|vyska|height|Vyska|VySkaz|Vel\.)[\s:]*(\d+)\s*cm',
            ],
            
            # Pulz
            'pulse': [
                r'(?:pulz|pulse|P|PF|TF)[\s:]*(\d+)\s*(?:/min|min|bpm)',
            ],
            
            # Teplota
            'temperature': [
                r'(?:teplota|temperature|T|TT)[\s:]*(\d+[.,]?\d*)\s*(?:°C|C|stupňov)',
            ],
            
            # Dátum
            'date': [
                r'(\d{1,2})[./\s](\d{1,2})[./\s](\d{4})',
                r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',
                r'(\d{1,2})[./](\d{1,2})[./](\d{2})\b',  # dd.mm.yy
            ],
        }
    
    def extract_health_metrics(self, text: str) -> List[Dict]:
        """
        Extrahuje zdravotné metriky z textu
        
        Args:
            text: Text dokumentu
            
        Returns:
            Zoznam nájdených metrík
        """
        metrics = []
        lines = text.split('\n')
        
        # Najprv hľadáme bloky s dátumom (kontextová extrakcia)
        for i, line in enumerate(lines):
            # Nájsť dátum
            date = self._extract_date(line)
            if date:
                # Ak našiel dátum, extrahuj hodnoty z tohto riadku + nasledujúcich 20 riadkov
                # (pre viacsťránkové výsledky)
                context_lines = lines[i:min(i+21, len(lines))]
                for context_line in context_lines:
                    line_metrics = self._extract_from_line(context_line, date)
                    if line_metrics:
                        metrics.extend(line_metrics)
        
        # Potom extrahovať zvyšné bez dátumu (aby sme nezmeškali nič)
        for line in lines:
            line_metrics = self._extract_from_line(line, None)
            # Pridať len ak ešte nemáme tento záznam
            for metric in line_metrics:
                if not any(m['raw_text'] == metric['raw_text'] for m in metrics):
                    metrics.append(metric)
        
        print(f"[EXTRACTOR] Found {len(metrics)} health metrics")
        
        # Uložiť extrahované dáta
        self._save_extracted_data(metrics)
        
        return metrics
    
    def _extract_from_line(self, line: str, context_date: Optional[str] = None) -> List[Dict]:
        """Extrahuje metriky z jedného riadku
        
        Args:
            line: Riadok textu
            context_date: Dátum z kontextu (z predchádzajúcich riadkov)
        """
        results = []
        
        # Extrahovať dátum z tohto riadku
        line_date = self._extract_date(line)
        # Použiť dátum z riadku ak je, inak dátum z kontextu
        date = line_date or context_date
        
        # Extrahovať každú metriku
        for metric_name, patterns in self.patterns.items():
            if metric_name == 'date':
                continue
                
            for pattern in patterns:
                matches = re.search(pattern, line, re.IGNORECASE)
                if matches:
                    value = self._parse_metric_value(metric_name, matches)
                    if value is not None:
                        results.append({
                            'metric': metric_name,
                            'value': value,
                            'date': date,
                            'raw_text': line.strip()
                        })
                    break
        
        return results
    
    def _extract_date(self, text: str) -> Optional[str]:
        """Extrahuje dátum z textu"""
        for pattern in self.patterns['date']:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                try:
                    # Pokus o vytvorenie dátumu
                    if len(groups) == 3:
                        # Európsky formát: DD.MM.YYYY alebo DD/MM/YYYY
                        if len(groups[2]) == 4:  # Rok má 4 cifry
                            day = int(groups[0])
                            month = int(groups[1])
                            year = int(groups[2])
                        elif len(groups[2]) == 2:  # DD.MM.YY
                            day = int(groups[0])
                            month = int(groups[1])
                            year = int(groups[2])
                            # Konvertovať 2-ciferný rok na 4-ciferný
                            year = 1900 + year if year >= 90 else 2000 + year
                        else:
                            continue
                        
                        # Vytvoriť dátum (deň, mesiac, rok)
                        date_obj = datetime(year, month, day)
                        return date_obj.strftime('%Y-%m-%d')
                except Exception as e:
                    # Debug len ak nie je štandardná chyba formátu
                    if "out of range" not in str(e):
                        print(f"[DATE ERROR] Cannot parse date from {groups}: {e}")
                    continue
        return None
    
    def _parse_metric_value(self, metric_name: str, matches) -> Optional[float]:
        """Parsuje hodnotu metriky z regex match"""
        try:
            if metric_name == 'blood_pressure':
                systolic = float(matches.group(1))
                diastolic = float(matches.group(2))
                return {'systolic': systolic, 'diastolic': diastolic}
            else:
                value_str = matches.group(1).replace(',', '.')
                return float(value_str)
        except:
            return None
    
    def _save_extracted_data(self, metrics: List[Dict]):
        """Uloží extrahované dáta do JSON súboru"""
        if not metrics:
            return
        
        output_file = settings.PROCESSED_DATA_DIR / f"extracted_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        
        print(f"Saved {len(metrics)} metrics to {output_file}")
