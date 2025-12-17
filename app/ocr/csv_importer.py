import csv
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict

from app.config import settings

class CSVImporter:
    """Importuje zdravotné dáta z CSV súboru"""
    
    def import_from_csv(self, csv_path: Path) -> List[Dict]:
        """
        Importuje dáta z CSV súboru
        
        Formát CSV:
        date,metric,value,unit
        2024-01-15,blood_pressure_systolic,125,mmHg
        2024-01-15,glucose,5.4,mmol/L
        """
        metrics = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Spracovať krvný tlak špeciálne
                if 'blood_pressure' in row['metric']:
                    metrics.append(self._process_blood_pressure_row(row))
                else:
                    metrics.append({
                        'metric': row['metric'],
                        'value': float(row['value']),
                        'date': row['date'],
                        'raw_text': f"Manual entry: {row['metric']} = {row['value']} {row.get('unit', '')}"
                    })
        
        # Uložiť importované dáta
        self._save_imported_data(metrics, csv_path.stem)
        
        return metrics
    
    def _process_blood_pressure_row(self, row: Dict) -> Dict:
        """Spracuje riadok s krvným tlakom"""
        # CSV môže mať systolic/diastolic oddelene
        # Vráti sa ako samostatná hodnota, systém ich spojí neskôr
        return {
            'metric': 'blood_pressure',
            'value': float(row['value']),
            'date': row['date'],
            'bp_type': 'systolic' if 'systolic' in row['metric'] else 'diastolic',
            'raw_text': f"Manual entry: {row['metric']} = {row['value']} mmHg"
        }
    
    def _save_imported_data(self, metrics: List[Dict], filename: str):
        """Uloží importované dáta"""
        output_file = settings.PROCESSED_DATA_DIR / f"csv_import_{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        
        print(f"Imported {len(metrics)} metrics from CSV to {output_file}")
    
    def create_template_csv(self, output_path: Path):
        """Vytvorí vzorový CSV súbor"""
        template_data = [
            {'date': '2024-01-15', 'metric': 'blood_pressure_systolic', 'value': '125', 'unit': 'mmHg'},
            {'date': '2024-01-15', 'metric': 'blood_pressure_diastolic', 'value': '82', 'unit': 'mmHg'},
            {'date': '2024-01-15', 'metric': 'glucose', 'value': '5.4', 'unit': 'mmol/L'},
            {'date': '2024-01-15', 'metric': 'cholesterol', 'value': '4.8', 'unit': 'mmol/L'},
            {'date': '2024-01-15', 'metric': 'ldl', 'value': '2.8', 'unit': 'mmol/L'},
            {'date': '2024-01-15', 'metric': 'hdl', 'value': '1.3', 'unit': 'mmol/L'},
            {'date': '2024-01-15', 'metric': 'triglycerides', 'value': '1.5', 'unit': 'mmol/L'},
            {'date': '2024-01-15', 'metric': 'bmi', 'value': '24.5', 'unit': ''},
        ]
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['date', 'metric', 'value', 'unit'])
            writer.writeheader()
            writer.writerows(template_data)
        
        print(f"Template CSV created at {output_path}")
