import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
import json

from app.config import settings
from app.database import get_session, HealthRecord

logger = logging.getLogger(__name__)


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


class HealthMetricsAnalyzer:
    def __init__(self):
        self.data = self._load_all_data()

    def _load_all_data(self) -> pd.DataFrame:
        all_metrics = []

        # Legacy JSON files
        for json_file in settings.PROCESSED_DATA_DIR.glob("extracted_data_*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    all_metrics.extend(json.load(f))
            except Exception as e:
                logger.warning('Error loading %s: %s', json_file, e)

        # DB health records (ocr + manual)
        try:
            session = get_session()
            db_records = (
                session.query(HealthRecord)
                .filter(HealthRecord.source.in_(["manual", "ocr"]))
                .all()
            )
            for record in db_records:
                value = record.value
                if value and '/' in str(value):
                    parts = str(value).split('/')
                    try:
                        value = {'systolic': float(parts[0]), 'diastolic': float(parts[1])}
                    except Exception:
                        value = _to_float(parts[0])
                else:
                    value = _to_float(value)
                all_metrics.append({
                    'metric': record.metric_type,
                    'value': value,
                    'date': record.record_date,
                    'unit': record.unit,
                    'source': record.source,
                })
            session.close()
        except Exception as e:
            logger.warning('Error loading health records from DB: %s', e)

        if not all_metrics:
            return pd.DataFrame()

        df = pd.DataFrame(all_metrics)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
        return df

    def _refresh(self):
        """Reload from DB to pick up data added after startup."""
        self.data = self._load_all_data()

    def get_latest_metrics(self) -> Dict:
        self._refresh()
        if self.data.empty:
            return {"error": "No data available"}

        latest_metrics = {}
        for metric_name in self.data['metric'].unique():
            metric_data = self.data[self.data['metric'] == metric_name].dropna(subset=['date'])
            if not metric_data.empty:
                row = metric_data.sort_values('date').iloc[-1]
                latest_metrics[metric_name] = {
                    'value': row['value'],
                    'date': row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None,
                    'status': self._get_metric_status(metric_name, row['value']),
                }
        return latest_metrics

    def get_metrics_history(self, days: int = 365) -> Dict:
        if self.data.empty:
            return {"error": "No data available"}

        cutoff = datetime.now() - timedelta(days=days)
        recent = self.data[self.data['date'] >= cutoff]
        history = {}
        for metric_name in recent['metric'].unique():
            metric_data = recent[recent['metric'] == metric_name].sort_values('date')
            history[metric_name] = [
                {
                    'date': row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None,
                    'value': row['value'],
                }
                for _, row in metric_data.iterrows()
            ]
        return history

    def get_comprehensive_summary(self) -> Dict:
        if self.data.empty:
            return {"error": "No data available"}

        latest = self.get_latest_metrics()
        return {
            'generated_at': datetime.now().isoformat(),
            'latest_metrics': latest,
            'health_score': self._calculate_health_score(latest),
            'alerts': self._generate_alerts(latest),
            'recommendations': self._generate_basic_recommendations(latest),
        }

    def _get_metric_status(self, metric_name: str, value) -> str:
        if value is None:
            return "unknown"
        if metric_name == 'blood_pressure' and isinstance(value, dict):
            sys = value.get('systolic', 0)
            dia = value.get('diastolic', 0)
            if sys >= 140 or dia >= 90:
                return "alert"
            if sys >= 130 or dia >= 80:
                return "warning"
            return "normal"
        thresholds = {
            'glucose': {'warning': 5.6, 'alert': 7.0},
            'hba1c': {'warning': 5.7, 'alert': 6.5},
            'cholesterol': {'warning': 5.2, 'alert': 6.2},
            'ldl': {'warning': 3.0, 'alert': 4.0},
            'triglycerides': {'warning': 1.7, 'alert': 2.3},
            'bmi': {'warning': 25, 'alert': 30},
        }
        if metric_name in thresholds and isinstance(value, (int, float)):
            if value >= thresholds[metric_name]['alert']:
                return "alert"
            if value >= thresholds[metric_name]['warning']:
                return "warning"
        return "normal"

    def _calculate_health_score(self, latest_metrics: Dict) -> int:
        if not latest_metrics or 'error' in latest_metrics:
            return 0
        score = 100
        for data in latest_metrics.values():
            status = data.get('status', 'normal')
            if status == 'alert':
                score -= 15
            elif status == 'warning':
                score -= 5
        return max(0, min(100, score))

    def _generate_alerts(self, latest_metrics: Dict) -> list:
        if not latest_metrics or 'error' in latest_metrics:
            return []
        alerts = []
        for metric_name, data in latest_metrics.items():
            status = data.get('status')
            value = data.get('value')
            if status == 'alert':
                alerts.append({
                    'severity': 'high',
                    'metric': metric_name,
                    'message': f'{metric_name} je výrazne nad normou',
                    'value': value,
                    'recommendation': f'Konzultujte s lekárom ohľadom {metric_name}',
                })
            elif status == 'warning':
                alerts.append({
                    'severity': 'medium',
                    'metric': metric_name,
                    'message': f'{metric_name} je mierne zvýšený',
                    'value': value,
                    'recommendation': f'Monitorujte {metric_name} a zvážte úpravu životného štýlu',
                })
        return alerts

    def _generate_basic_recommendations(self, latest_metrics: Dict) -> list:
        if not latest_metrics or 'error' in latest_metrics:
            return []
        recs = [{'category': 'general', 'title': 'Pravidelné kontroly',
                 'description': 'Odporúčame pravidelnú kontrolu zdravotného stavu'}]
        if 'glucose' in latest_metrics or 'hba1c' in latest_metrics:
            recs.append({'category': 'diabetes_prevention', 'title': 'Kontrola glykémie',
                         'description': 'Monitorujte hladiny cukru a zvážte konzultáciu s diabetológom'})
        if 'blood_pressure' in latest_metrics:
            recs.append({'category': 'cardiovascular', 'title': 'Kardiovaskulárne zdravie',
                         'description': 'Pravidelne kontrolujte krvný tlak a konzultujte s kardiológom'})
        return recs
