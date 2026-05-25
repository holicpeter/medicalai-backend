import json
import logging
import re
from datetime import datetime, date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthDataExtractor:
    def __init__(self):
        pass

    def extract_health_metrics(self, text: str) -> List[Dict]:
        """Parse Claude's JSON response, fall back to regex if needed."""
        metrics = self._parse_json(text)
        if not metrics:
            logger.warning('JSON parsing returned 0 metrics, trying regex fallback')
            metrics = self._regex_extract(text)

        logger.info('Found %d health metrics', len(metrics))
        self._save_extracted_data(metrics)
        return metrics

    # ------------------------------------------------------------------ #
    # JSON parsing (primary)
    # ------------------------------------------------------------------ #

    def _parse_json(self, text: str) -> List[Dict]:
        """Extract JSON array from Claude's response."""
        try:
            text = text.strip()
            # Strip markdown code blocks if present
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            start = text.find('[')
            end = text.rfind(']') + 1
            if start == -1 or end <= start:
                return []
            raw = json.loads(text[start:end])
            metrics = []
            for item in raw:
                metric = item.get('metric', '').strip().lower().replace(' ', '_')
                raw_value = item.get('value')
                if not metric or raw_value is None:
                    continue
                # blood_pressure stored as "120/80" string
                if metric == 'blood_pressure' and isinstance(raw_value, str) and '/' in raw_value:
                    value = raw_value
                else:
                    try:
                        value = float(str(raw_value).replace(',', '.'))
                    except (ValueError, TypeError):
                        continue
                metrics.append({
                    'metric': metric,
                    'value': value,
                    'unit': item.get('unit', ''),
                    'date': item.get('date'),
                    'status': item.get('status', ''),
                    'raw_text': str(item),
                })
            return metrics
        except Exception as e:
            logger.warning('JSON parse error: %s', e)
            return []

    # ------------------------------------------------------------------ #
    # Regex fallback
    # ------------------------------------------------------------------ #

    def _regex_extract(self, text: str) -> List[Dict]:
        patterns = {
            'blood_pressure': [
                r'(?:tlak|TK|BP|RR|krvn[yý]\s+tlak)[\s:]*(\d{2,3})\s*/\s*(\d{2,3})',
                r'(\d{2,3})\s*/\s*(\d{2,3})\s*(?:mmHg|mm Hg)',
            ],
            'glucose': [
                r'(?:S_Gluk[oó]za|glukoza|glyk[eé]mia|glucose|GLU|Glu)[\s:]*(?:OK|A|)?\s*(\d+[.,]\d+)',
            ],
            'hba1c': [r'HbA1c[\s:]*(\d+[.,]?\d*)'],
            'cholesterol': [
                r'(?:S_Cholesterol|cholesterol|CHOL)[\s:]*(?:OK|A|)?\s*(\d+[.,]\d+)',
            ],
            'ldl': [
                r'(?:S_LDL[-\s]?chol?|LDL[-\s]?chol?|LDL)[\s:]*(?:OK|A|)?\s*(\d+[.,]\d+)',
            ],
            'hdl': [
                r'(?:S_HDL[-\s]?chol?|HDL[-\s]?chol?|HDL)[\s:]*(?:OK|A|)?\s*(\d+[.,]\d+)',
            ],
            'triglycerides': [
                r'(?:S_Triacylglyceroly|triglyceridy|TG|TAG)[\s:]*(?:OK|A|)?\s*(\d+[.,]\d+)',
            ],
            'creatinine': [
                r'(?:S_Kreat[ií]n[ií]n|kreatinin|KREAT|Krea)[\s:]*(?:OK|A|)?\s*(\d+[.,]?\d*)',
            ],
            'alt': [r'(?:S_ALT|ALT|ALAT|SGPT)[\s:]*(?:OK|A|)?\s*(\d+[.,]?\d*)'],
            'ast': [r'(?:S_AST|AST|ASAT|SGOT)[\s:]*(?:OK|A|)?\s*(\d+[.,]?\d*)'],
            'ggt': [r'(?:S_GGT|GGT)[\s:]*(?:OK|A|)?\s*(\d+[.,]?\d*)'],
            'hemoglobin': [
                r'(?:B_Hemoglobin\s+HGB|hemoglobin|Hb|HGB)[\s:]*(?:\(A\)|OK|A|)?\s*(\d+[.,]?\d*)',
            ],
            'leukocytes': [
                r'(?:B_Leukocyty\s+WBC|leukocyty|WBC)[\s:]*(?:\(A\)|OK|A|)?\s*(\d+[.,]?\d*)',
            ],
            'platelets': [
                r'(?:B_Trombocyty\s+PLT|trombocyty|PLT)[\s:]*(?:\(A\)|OK|A|)?\s*(\d+[.,]?\d*)',
            ],
            'crp': [r'(?:S_CRP|CRP)[\s:]*(?:OK|A|)?\s*(\d+[.,]?\d*)'],
            'bmi': [r'BMI[\s:]*(\d+[.,]?\d*)'],
            'weight': [r'(?:hmotnost|vaha|weight|B_Hmotnosť)[\s:]*(\d+[.,]?\d*)\s*kg'],
        }

        metrics = []
        lines = text.split('\n')
        doc_date = None
        for line in lines:
            d = self._extract_date(line)
            if d:
                doc_date = d
                break

        for line in lines:
            line_date = self._extract_date(line) or doc_date
            for metric_name, pats in patterns.items():
                for pat in pats:
                    m = re.search(pat, line, re.IGNORECASE)
                    if m:
                        value = self._parse_value(metric_name, m)
                        if value is not None:
                            raw = line.strip()
                            if not any(x['raw_text'] == raw for x in metrics):
                                metrics.append({
                                    'metric': metric_name,
                                    'value': value,
                                    'date': line_date,
                                    'raw_text': raw,
                                })
                        break
        return metrics

    def _extract_date(self, text: str) -> Optional[str]:
        for pat in [r'(\d{1,2})[./](\d{1,2})[./](\d{4})', r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})']:
            m = re.search(pat, text)
            if m:
                g = m.groups()
                try:
                    if len(g[2]) == 4:
                        return datetime(int(g[2]), int(g[1]), int(g[0])).strftime('%Y-%m-%d')
                    return datetime(int(g[0]), int(g[1]), int(g[2])).strftime('%Y-%m-%d')
                except Exception:
                    continue
        return None

    def _parse_value(self, metric_name: str, m) -> Optional[float]:
        try:
            if metric_name == 'blood_pressure':
                return {'systolic': float(m.group(1)), 'diastolic': float(m.group(2))}
            return float(m.group(1).replace(',', '.'))
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Database persistence
    # ------------------------------------------------------------------ #

    def _save_extracted_data(self, metrics: List[Dict]):
        if not metrics:
            return
        try:
            from app.database import get_session, HealthRecord
            session = get_session()
            saved = 0
            for metric in metrics:
                try:
                    value = metric.get('value')
                    if value is None:
                        continue
                    if isinstance(value, dict):
                        value_str = f"{value.get('systolic')}/{value.get('diastolic')}"
                    else:
                        value_str = str(value)
                    metric_date = metric.get('date')
                    record_date = (
                        datetime.strptime(metric_date, '%Y-%m-%d').date()
                        if metric_date else date.today()
                    )
                    record = HealthRecord(
                        patient_id=1,
                        record_type='lab_test',
                        record_date=record_date,
                        source='ocr',
                        metric_type=metric.get('metric'),
                        value=value_str,
                        unit=metric.get('unit', ''),
                        notes=metric.get('raw_text', '')[:500],
                    )
                    session.add(record)
                    saved += 1
                except Exception as e:
                    logger.warning('Error saving metric: %s', e)
            session.commit()
            session.close()
            logger.info('Saved %d metrics to database', saved)
        except Exception as e:
            logger.error('Database error: %s', e)
