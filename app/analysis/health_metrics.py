import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional

from app.analysis.sources import load_all_measurements
from app.i18n import tr

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
    def __init__(self, patient_id: int):
        self.patient_id = patient_id
        self.data = self._load_all_data()

    def _load_all_data(self) -> pd.DataFrame:
        all_metrics = []

        # Legacy JSON files (extracted_data_*.json) predate the database and
        # per-patient scoping entirely — a flat file on disk with no
        # patient_id of its own. Now that patient_id is the isolation
        # boundary between tenants, a file with no owner cannot be safely
        # attributed to any one of them; showing it to every patient would be
        # a cross-tenant leak of whatever it contains. Deliberately dropped
        # here rather than guessed at — all of this app's actual data has
        # lived in the database for a long time (see the DB-backed loaders
        # below), so nothing observable should depend on this path still
        # existing. If it turns out something does, that data needs a real
        # patient_id and an import into the database, not a reason to bring
        # this branch back.
        all_metrics.extend(load_all_measurements(self.patient_id))

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
            # An empty account is a normal state, not an error. Returning a
            # different shape here (an {"error": ...} object instead of the
            # metric map) breaks every caller that iterates the result.
            return {}

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
        self._refresh()
        if self.data.empty:
            return {}

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
        latest = self.get_latest_metrics()
        if not latest:
            return {
                'generated_at': datetime.now().isoformat(),
                'latest_metrics': {},
                'health_score': 0,
                'alerts': [],
                'recommendations': [],
                'has_data': False,
            }

        return {
            'generated_at': datetime.now().isoformat(),
            'latest_metrics': latest,
            'health_score': self._calculate_health_score(latest),
            'alerts': self._generate_alerts(latest),
            'recommendations': self._generate_basic_recommendations(latest),
            'has_data': True,
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
                    'message': tr(f'{metric_name} je výrazne nad normou', f'{metric_name} is well above the normal range'),
                    'value': value,
                    'recommendation': tr(f'Konzultujte s lekárom ohľadom {metric_name}', f'Talk to your doctor about {metric_name}'),
                })
            elif status == 'warning':
                alerts.append({
                    'severity': 'medium',
                    'metric': metric_name,
                    'message': tr(f'{metric_name} je mierne zvýšený', f'{metric_name} is slightly raised'),
                    'value': value,
                    'recommendation': tr(f'Monitorujte {metric_name} a zvážte úpravu životného štýlu', f'Keep an eye on {metric_name} and consider lifestyle changes'),
                })
        return alerts

    def _generate_basic_recommendations(self, latest_metrics: Dict) -> list:
        if not latest_metrics or 'error' in latest_metrics:
            return []
        recs = [{'category': 'general', 'title': tr('Pravidelné kontroly', 'Regular check-ups'),
                 'description': tr('Odporúčame pravidelnú kontrolu zdravotného stavu',
                                   'We recommend regular health check-ups')}]
        if 'glucose' in latest_metrics or 'hba1c' in latest_metrics:
            recs.append({'category': 'diabetes_prevention', 'title': tr('Kontrola glykémie', 'Blood sugar check'),
                         'description': tr('Monitorujte hladiny cukru a zvážte konzultáciu s diabetológom',
                                           'Monitor your blood sugar and consider seeing a diabetologist')})
        if 'blood_pressure' in latest_metrics:
            recs.append({'category': 'cardiovascular', 'title': tr('Kardiovaskulárne zdravie', 'Heart health'),
                         'description': tr('Pravidelne kontrolujte krvný tlak a konzultujte s kardiológom',
                                           'Check your blood pressure regularly and talk to a cardiologist')})
        return recs
