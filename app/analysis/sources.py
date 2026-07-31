"""Shared loading of health measurements from the database.

Both analyzers need the same rows in the same shape. They used to build that
separately, and the two implementations drifted: the trend analyzer read Apple
Health data while the dashboard analyzer did not, so an Apple Health import
showed up in trends but left the dashboard reading zero.
"""
import logging
from typing import Dict, List

from app.analysis.units import normalize
from app.database import get_session, HealthRecord, AppleHealthData

logger = logging.getLogger(__name__)

# Apple Health identifiers that map onto the metrics the app interprets.
# Types outside this map (step counts, distances, …) are handled by the
# activity endpoints instead.
APPLE_TO_METRIC = {
    'HKQuantityTypeIdentifierBodyMass': 'weight',
    'HKQuantityTypeIdentifierHeight': 'height',
    'HKQuantityTypeIdentifierHeartRate': 'heart_rate',
    'HKQuantityTypeIdentifierRestingHeartRate': 'heart_rate',
    'HKQuantityTypeIdentifierBloodPressureSystolic': 'blood_pressure_systolic',
    'HKQuantityTypeIdentifierBloodPressureDiastolic': 'blood_pressure_diastolic',
    'HKQuantityTypeIdentifierBodyMassIndex': 'bmi',
    'HKQuantityTypeIdentifierBloodGlucose': 'glucose',
    'HKQuantityTypeIdentifierBodyFatPercentage': 'body_fat',
    'HKQuantityTypeIdentifierOxygenSaturation': 'oxygen_saturation',
}


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def _parse_value(raw):
    """Blood pressure is stored as "120/80"; everything else is a number."""
    if raw and '/' in str(raw):
        parts = str(raw).split('/')
        try:
            return {'systolic': float(parts[0]), 'diastolic': float(parts[1])}
        except Exception:
            return _to_float(parts[0])
    return _to_float(raw)


def load_health_records() -> List[Dict]:
    """Manually entered and OCR-extracted records."""
    metrics = []
    session = get_session()
    try:
        records = (
            session.query(HealthRecord)
            .filter(HealthRecord.source.in_(["manual", "ocr"]))
            .all()
        )
        for record in records:
            metric_type = 'heart_rate' if record.metric_type == 'pulse' else record.metric_type
            value, unit = normalize(metric_type, _parse_value(record.value), record.unit)
            metrics.append({
                'metric': metric_type,
                'value': value,
                'date': record.record_date,
                'unit': unit,
                'source': record.source,
            })
        logger.info('Loaded %d health records from database (ocr + manual)', len(records))
    except Exception as e:
        logger.warning('Error loading health records from DB: %s', e)
    finally:
        session.close()
    return metrics


def load_apple_health() -> List[Dict]:
    """Apple Health measurements that map onto interpreted metrics."""
    metrics = []
    session = get_session()
    try:
        records = (
            session.query(AppleHealthData)
            .filter(AppleHealthData.record_type.in_(list(APPLE_TO_METRIC.keys())))
            .all()
        )
        for record in records:
            metric_name = APPLE_TO_METRIC.get(record.record_type)
            if not metric_name or record.value is None:
                continue
            # Apple Health reports in the phone's regional units.
            value, unit = normalize(metric_name, float(record.value), record.unit)
            metrics.append({
                'metric': metric_name,
                'value': value,
                'date': record.start_date,
                'unit': unit,
                'source': 'apple_health',
            })
        logger.info('Loaded %d Apple Health records', len(records))
    except Exception as e:
        logger.warning('Error loading Apple Health records: %s', e)
    finally:
        session.close()
    return metrics


def load_all_measurements() -> List[Dict]:
    """Every measurement the app can interpret, from all stored sources."""
    return load_health_records() + load_apple_health()
