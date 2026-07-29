"""Normalise measurements to one canonical unit per metric.

Records arrive from several sources that disagree on units — Apple Health uses
whatever the phone's region is set to (mg/dL or mmol/L for glucose, kg or lb for
weight), lab reports use SI, and manual entries use whatever was typed. The
interpretation thresholds are SI-only, so an unconverted mg/dL glucose reading
of 90 was compared against a 5.6 mmol/L ceiling and reported as far above
normal, when it is in fact 5.0 mmol/L and perfectly normal.
"""
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CANONICAL = {
    'glucose': 'mmol/l',
    'cholesterol': 'mmol/l',
    'ldl': 'mmol/l',
    'hdl': 'mmol/l',
    'triglycerides': 'mmol/l',
    'weight': 'kg',
    'height': 'cm',
    'hba1c': '%',
    'bmi': 'kg/m2',
    'heart_rate': 'bpm',
    'blood_pressure': 'mmhg',
    'blood_pressure_systolic': 'mmhg',
    'blood_pressure_diastolic': 'mmhg',
}

# Molar masses differ per analyte, so mg/dL -> mmol/L is not one constant.
_MGDL_TO_MMOL = {
    'glucose': 1 / 18.0182,
    'cholesterol': 1 / 38.67,
    'ldl': 1 / 38.67,
    'hdl': 1 / 38.67,
    'triglycerides': 1 / 88.57,
}

_MASS_TO_KG = {
    'kg': 1.0,
    'g': 0.001,
    'lb': 0.45359237,
    'lbs': 0.45359237,
    'pound': 0.45359237,
    'st': 6.35029318,
    'stone': 6.35029318,
}

_LENGTH_TO_CM = {
    'cm': 1.0,
    'm': 100.0,
    'mm': 0.1,
    'in': 2.54,
    'inch': 2.54,
    'ft': 30.48,
}


def _clean(unit: Optional[str]) -> str:
    """Normalise unit spelling.

    Apple Health writes localised forms such as 'mmol<T>/L' and 'count/min'.
    """
    if not unit:
        return ''
    u = unit.strip().lower()
    u = re.sub(r'<[^>]*>', '', u)      # mmol<T>/L -> mmol/L
    u = u.replace('µ', 'u').replace('μ', 'u')
    u = re.sub(r'\s+', '', u)
    return u


def normalize(metric: str, value, unit: Optional[str]) -> Tuple[object, str]:
    """Return (value, unit) converted to the canonical unit for the metric.

    Values whose unit is unknown or absent are returned unchanged — guessing
    would be worse than leaving a reading alone.
    """
    canonical = CANONICAL.get(metric)
    if canonical is None or value is None:
        return value, _clean(unit) or (unit or '')

    u = _clean(unit)

    # Blood pressure arrives as {'systolic': .., 'diastolic': ..}; mmHg is the
    # only unit in practice, so pass it through rather than touching the dict.
    if isinstance(value, dict):
        return value, canonical

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value, u or canonical

    if not u or u == canonical:
        return numeric, canonical

    if metric in _MGDL_TO_MMOL:
        if u in ('mg/dl', 'mgdl', 'mg/100ml'):
            return numeric * _MGDL_TO_MMOL[metric], canonical
        if u in ('mmol/l', 'mmoll'):
            return numeric, canonical

    if metric == 'weight' and u in _MASS_TO_KG:
        return numeric * _MASS_TO_KG[u], canonical

    if metric == 'height' and u in _LENGTH_TO_CM:
        return numeric * _LENGTH_TO_CM[u], canonical

    if metric == 'heart_rate' and u in ('count/min', 'bpm', 'min-1', '1/min'):
        return numeric, canonical

    if metric == 'hba1c' and u in ('%', 'percent'):
        return numeric, canonical

    logger.debug('No conversion for %s in %r — leaving value as-is', metric, unit)
    return numeric, u
