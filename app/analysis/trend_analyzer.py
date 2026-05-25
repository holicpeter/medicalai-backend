import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pathlib import Path
import json

from app.config import settings
from app.database import get_session, HealthRecord, AppleHealthData

logger = logging.getLogger(__name__)


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


class TrendAnalyzer:
    _data_cache = None
    _cache_timestamp = None
    _cache_ttl = 300  # seconds

    def __init__(self):
        if (
            TrendAnalyzer._data_cache is not None
            and TrendAnalyzer._cache_timestamp
            and (datetime.now() - TrendAnalyzer._cache_timestamp).total_seconds() < TrendAnalyzer._cache_ttl
        ):
            self.data = TrendAnalyzer._data_cache
            logger.debug('Using cached trend data (%d rows)', len(self.data))
        else:
            self.data = self._load_data()
            TrendAnalyzer._data_cache = self.data
            TrendAnalyzer._cache_timestamp = datetime.now()
            logger.info('Loaded fresh trend data (%d rows), cached for %ds', len(self.data), TrendAnalyzer._cache_ttl)

    def _load_data(self) -> pd.DataFrame:
        all_metrics = []

        # 1. Legacy JSON files (older exports)
        for json_file in settings.PROCESSED_DATA_DIR.glob("extracted_data_*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    metrics = json.load(f)
                    for metric in metrics:
                        if metric.get('metric') == 'pulse':
                            metric['metric'] = 'heart_rate'
                    all_metrics.extend(metrics)
            except Exception as e:
                logger.warning('Error loading %s: %s', json_file, e)

        # 2. Health records from DB — both OCR-extracted and manually entered
        try:
            session = get_session()
            db_records = (
                session.query(HealthRecord)
                .filter(HealthRecord.source.in_(["manual", "ocr"]))
                .all()
            )
            logger.info('Loaded %d health records from database (ocr + manual)', len(db_records))
            for record in db_records:
                metric_type = record.metric_type
                if metric_type == 'pulse':
                    metric_type = 'heart_rate'
                # Parse blood-pressure stored as "120/80"
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
                    'date': record.record_date,
                    'metric': metric_type,
                    'value': value,
                    'unit': record.unit,
                    'source': record.source,
                })
            session.close()
        except Exception as e:
            logger.warning('Error loading health records from DB: %s', e)

        # 3. Apple Health data
        try:
            session = get_session()
            apple_to_metric_map = {
                'HKQuantityTypeIdentifierBodyMass': 'weight',
                'HKQuantityTypeIdentifierHeight': 'height',
                'HKQuantityTypeIdentifierHeartRate': 'heart_rate',
                'HKQuantityTypeIdentifierBloodPressureSystolic': 'blood_pressure_systolic',
                'HKQuantityTypeIdentifierBloodPressureDiastolic': 'blood_pressure_diastolic',
                'HKQuantityTypeIdentifierBodyMassIndex': 'bmi',
                'HKQuantityTypeIdentifierBloodGlucose': 'glucose',
            }
            apple_records = session.query(AppleHealthData).filter(
                AppleHealthData.record_type.in_(list(apple_to_metric_map.keys()))
            ).all()
            logger.info('Loaded %d Apple Health records', len(apple_records))
            for record in apple_records:
                metric_name = apple_to_metric_map.get(record.record_type)
                if metric_name and record.value is not None:
                    all_metrics.append({
                        'date': record.start_date,
                        'metric': metric_name,
                        'value': float(record.value),
                        'unit': record.unit,
                        'source': 'apple_health',
                    })
            session.close()
        except Exception as e:
            logger.warning('Error loading Apple Health records: %s', e)

        if not all_metrics:
            return pd.DataFrame()

        df = pd.DataFrame(all_metrics)

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')

        if 'value' in df.columns:
            # Leave dict values (blood_pressure) as-is; coerce strings to numeric
            def _safe_numeric(v):
                if isinstance(v, dict):
                    return v
                try:
                    return float(str(v).replace(',', '.'))
                except Exception:
                    return None
            df['value'] = df['value'].apply(_safe_numeric)

        df = df.dropna(subset=['date'])
        df = df.sort_values('date')
        logger.info('Total trend rows after loading: %d', len(df))
        return df

    def analyze_trends(
        self,
        metric: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict:
        if self.data.empty:
            return {
                "trends": {},
                "message": "Zatiaľ nie sú k dispozícii žiadne dáta. Pridajte zdravotné záznamy manuálne alebo nahrajte dokumenty.",
            }

        df = self.data.copy()

        if 'date' in df.columns:
            if start_date:
                try:
                    df = df[df['date'] >= pd.to_datetime(start_date)]
                except Exception:
                    pass
            if end_date:
                try:
                    df = df[df['date'] <= pd.to_datetime(end_date)]
                except Exception:
                    pass

        if metric:
            df = df[df['metric'] == metric]

        if df.empty:
            return {"error": "No data found", "message": "Žiadne dáta pre zvolené filtre"}

        trends = {}
        for metric_name in df['metric'].unique():
            try:
                trend = self._analyze_single_metric(metric_name, df[df['metric'] == metric_name])
                trends[metric_name] = trend
            except Exception as e:
                logger.warning('Cannot analyze %s: %s', metric_name, e)
                trends[metric_name] = {"error": str(e)}

        return trends

    def _analyze_single_metric(self, metric_name: str, data: pd.DataFrame) -> Dict:
        if data.empty:
            return {"error": "No data for this metric"}

        if metric_name == 'blood_pressure':
            return self._analyze_blood_pressure(data)

        values = data['value'].dropna()
        numeric_values = [float(v) for v in values if isinstance(v, (int, float))]

        if not numeric_values:
            return {"error": "No numeric values"}

        return {
            'count': len(numeric_values),
            'latest': float(numeric_values[-1]),
            'mean': float(np.mean(numeric_values)),
            'min': float(np.min(numeric_values)),
            'max': float(np.max(numeric_values)),
            'std': float(np.std(numeric_values)) if len(numeric_values) > 1 else 0,
            'trend': self._calculate_trend(data),
            'values_over_time': self._get_values_over_time(data),
            'interpretation': self._interpret_metric(metric_name, {'latest': float(numeric_values[-1])}),
        }

    def _analyze_blood_pressure(self, data: pd.DataFrame) -> Dict:
        systolic_values, diastolic_values = [], []
        for _, row in data.iterrows():
            if isinstance(row['value'], dict):
                systolic_values.append(row['value'].get('systolic'))
                diastolic_values.append(row['value'].get('diastolic'))
        return {
            'systolic': {
                'latest': systolic_values[-1] if systolic_values else None,
                'mean': float(np.mean(systolic_values)) if systolic_values else None,
                'min': float(np.min(systolic_values)) if systolic_values else None,
                'max': float(np.max(systolic_values)) if systolic_values else None,
            },
            'diastolic': {
                'latest': diastolic_values[-1] if diastolic_values else None,
                'mean': float(np.mean(diastolic_values)) if diastolic_values else None,
                'min': float(np.min(diastolic_values)) if diastolic_values else None,
                'max': float(np.max(diastolic_values)) if diastolic_values else None,
            },
            'interpretation': self._interpret_blood_pressure(systolic_values, diastolic_values),
        }

    def _calculate_trend(self, data: pd.DataFrame) -> str:
        data_with_dates = data.dropna(subset=['date']).copy()
        if len(data_with_dates) < 2:
            return "insufficient_data"

        data_sorted = data_with_dates.sort_values('date')
        values = []
        for val in data_sorted['value'].values:
            if isinstance(val, (int, float)):
                values.append(float(val))
            elif isinstance(val, dict) and 'systolic' in val:
                values.append(float(val['systolic']))

        if len(values) < 2:
            return "insufficient_data"

        try:
            slope = np.polyfit(np.arange(len(values), dtype=np.float64), np.array(values, dtype=np.float64), 1)[0]
            if slope > 0.1:
                return "increasing"
            elif slope < -0.1:
                return "decreasing"
            return "stable"
        except Exception as e:
            logger.warning('Cannot calculate trend slope: %s', e)
            return "unknown"

    def _get_values_over_time(self, data: pd.DataFrame) -> List[Dict]:
        if 'date' not in data.columns:
            return []
        result = []
        for _, row in data.dropna(subset=['date']).sort_values('date').iterrows():
            val = row['value']
            if isinstance(val, dict):
                val = val.get('systolic')
            try:
                date_str = row['date'].strftime('%Y-%m-%d')
                result.append({'date': date_str, 'value': float(val)})
            except Exception:
                continue
        return result

    def _interpret_metric(self, metric_name: str, trend_data: Dict) -> str:
        latest = trend_data.get('latest')
        if latest is None:
            return "no_data"
        normal_ranges = {
            'glucose': (3.9, 5.6),
            'hba1c': (0, 5.7),
            'cholesterol': (0, 5.2),
            'ldl': (0, 3.0),
            'hdl': (1.0, float('inf')),
            'triglycerides': (0, 1.7),
            'bmi': (18.5, 24.9),
        }
        if metric_name in normal_ranges:
            lo, hi = normal_ranges[metric_name]
            if latest < lo:
                return "below_normal"
            if latest > hi:
                return "above_normal"
            return "normal"
        return "unknown"

    def _interpret_blood_pressure(self, systolic: List, diastolic: List) -> str:
        if not systolic or not diastolic:
            return "no_data"
        s, d = systolic[-1], diastolic[-1]
        if s < 120 and d < 80:
            return "normal"
        if s < 130 and d < 80:
            return "elevated"
        if s < 140 or d < 90:
            return "hypertension_stage_1"
        if s < 180 or d < 120:
            return "hypertension_stage_2"
        return "hypertension_crisis"

    def get_summary(self, trends: Dict) -> Dict:
        summary = {
            'total_metrics': len(trends),
            'metrics_analyzed': list(trends.keys()),
            'concerning_trends': [],
            'positive_trends': [],
        }
        for metric, data in trends.items():
            interpretation = str(data.get('interpretation', ''))
            if 'above_normal' in interpretation or 'hypertension' in interpretation:
                summary['concerning_trends'].append(metric)
            elif interpretation in ('normal', 'improving'):
                summary['positive_trends'].append(metric)
        return summary
