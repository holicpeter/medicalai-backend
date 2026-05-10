import re
from datetime import datetime, date
from typing import Dict, List, Optional


class HealthDataExtractor:
        def __init__(self):
                        self.patterns = self._init_patterns()

            def _init_patterns(self) -> Dict:
                            return {
                                                'blood_pressure': [
                                                                        r'(?:tlak|TK|BP|RR)[\s:]*(\d{2,3})\s*/\s*(\d{2,3})',
                                                                        r'(\d{2,3})\s*/\s*(\d{2,3})\s*(?:mmHg|mm Hg)',
                                                ],
                                                'glucose': [r'(?:glukoza|glykemia|glucose|GLU|Glu)[\s:]*(\d+[.,]?\d*)'],
                                                'hba1c': [r'HbA1c[\s:]*(\d+[.,]?\d*)'],
                                                'cholesterol': [r'(?:cholesterol|CHOL|Chol)[\s:]*(\d+[.,]?\d*)'],
                                                'ldl': [r'LDL[\s:]*(\d+[.,]?\d*)'],
                                                'hdl': [r'HDL[\s:]*(\d+[.,]?\d*)'],
                                                'triglycerides': [r'(?:triglyceridy|TG|TAG)[\s:]*(\d+[.,]?\d*)'],
                                                'creatinine': [r'(?:kreatinin|KREAT|Krea)[\s:]*(\d+[.,]?\d*)'],
                                                'alt': [r'(?:ALT|ALAT|SGPT)[\s:]*(\d+[.,]?\d*)'],
                                                'ast': [r'(?:AST|ASAT|SGOT)[\s:]*(\d+[.,]?\d*)'],
                                                'hemoglobin': [r'(?:hemoglobin|Hb|HGB)[\s:]*(\d+[.,]?\d*)'],
                                                'leukocytes': [r'(?:leukocyty|WBC)[\s:]*(\d+[.,]?\d*)'],
                                                'platelets': [r'(?:trombocyty|PLT)[\s:]*(\d+[.,]?\d*)'],
                                                'bmi': [r'BMI[\s:]*(\d+[.,]?\d*)'],
                                                'weight': [r'(?:hmotnost|vaha|weight)[\s:]*(\d+[.,]?\d*)\s*kg'],
                                                'date': [
                                                                        r'(\d{1,2})[./](\d{1,2})[./](\d{4})',
                                                                        r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',
                                                ],
                            }

    def extract_health_metrics(self, text: str) -> List[Dict]:
                    metrics = []
                    lines = text.split('\n')
                    for i, line in enumerate(lines):
                                        current_date = self._extract_date(line)
                                        context_lines = lines[i:min(i+21, len(lines))]
                                        for context_line in context_lines:
                                                                line_metrics = self._extract_from_line(context_line, current_date)
                                                                for metric in line_metrics:
                                                                                            if not any(m['raw_text'] == metric['raw_text'] for m in metrics):
                                                                                                                            metrics.append(metric)
                                                                                                            print('[EXTRACTOR] Found ' + str(len(metrics)) + ' health metrics')
                                                                                self._save_extracted_data(metrics)
                                                        return metrics

    def _extract_from_line(self, line: str, context_date=None) -> List[Dict]:
                    results = []
                    line_date = self._extract_date(line)
                    current_date = line_date or context_date
                    for metric_name, patterns in self.patterns.items():
                                        if metric_name == 'date':
                                                                continue
                                                            for pattern in patterns:
                                                                                    matches = re.search(pattern, line, re.IGNORECASE)
                                                                                    if matches:
                                                                                                                value = self._parse_metric_value(metric_name, matches)
                                                                                                                if value is not None:
                                                                                                                                                results.append({'metric': metric_name, 'value': value, 'date': current_date, 'raw_text': line.strip()})
                                                                                                                                            break
                                                                                                    return results

    def _extract_date(self, text: str) -> Optional[str]:
                    for pattern in self.patterns['date']:
                                        match = re.search(pattern, text)
                                        if match:
                                                                groups = match.groups()
                                                                try:
                                                                                            if len(groups[2]) == 4:
                                                                                                                            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                                                                        else:
                        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                    return datetime(year, month, day).strftime('%Y-%m-%d')
                except:
                    continue
        return None

    def _parse_metric_value(self, metric_name: str, matches) -> Optional[float]:
                    try:
                                        if metric_name == 'blood_pressure':
                                                                return {'systolic': float(matches.group(1)), 'diastolic': float(matches.group(2))}
    else:
                return float(matches.group(1).replace(',', '.'))
        except:
            return None

    def _save_extracted_data(self, metrics: List[Dict]):
                    if not metrics:
                                        return
                                    try:
                                                        from app.database import get_session, HealthRecord
                                                        session = get_session()
                                                        saved_count = 0
                                                        for metric in metrics:
                                                                                try:
                                                                                                            value = metric.get('value')
                                                                                                            if value is None:
                                                                                                                                            continue
                                                                                                                                        if isinstance(value, dict):
                                                                                                                                                                        value_str = str(value.get('systolic')) + '/' + str(value.get('diastolic'))
                                                                                        else:
                        value_str = str(value)
                    metric_date = metric.get('date')
                    if metric_date:
                                                    record_date = datetime.strptime(metric_date, '%Y-%m-%d').date()
else:
                        record_date = date.today()
                    record = HealthRecord(patient_id=1, record_type='lab_test', record_date=record_date, source='ocr', metric_type=metric.get('metric'), value=value_str, notes=metric.get('raw_text', '')[:500])
                    session.add(record)
                    saved_count += 1
except Exception as e:
                    print('[EXTRACTOR] Error: ' + str(e))
                    continue
            session.commit()
            session.close()
            print('[EXTRACTOR] Saved ' + str(saved_count) + ' metrics to database')
except Exception as e:
            print('[EXTRACTOR] Database error: ' + str(e))
