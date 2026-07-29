"""Measurements must reach the interpretation thresholds in SI units.

Apple Health reports in the phone's regional units, so a glucose reading can
arrive as mg/dL while the thresholds are mmol/L. Unconverted, a normal
90 mg/dL (5.0 mmol/L) was compared against a 5.6 ceiling and flagged as
above normal.
"""
import pytest

from app.analysis.units import normalize


@pytest.mark.parametrize("metric,value,unit,expected", [
    # mg/dL -> mmol/L uses a different factor per analyte
    ("glucose", 90, "mg/dL", 4.995),
    ("glucose", 180, "mg/dL", 9.99),
    ("cholesterol", 200, "mg/dL", 5.172),
    ("ldl", 100, "mg/dL", 2.586),
    ("hdl", 60, "mg/dL", 1.552),
    ("triglycerides", 150, "mg/dL", 1.694),
])
def test_mgdl_conversion(metric, value, unit, expected):
    got, canonical = normalize(metric, value, unit)
    assert canonical == "mmol/l"
    assert got == pytest.approx(expected, rel=1e-3)


def test_apple_health_localised_unit_string():
    # Apple Health writes 'mmol<T>/L'
    got, unit = normalize("glucose", 5.2, "mmol<T>/L")
    assert (got, unit) == (5.2, "mmol/l")


@pytest.mark.parametrize("value,unit,expected", [
    (70, "kg", 70),
    (154, "lb", 69.853),
    (11, "st", 69.853),
])
def test_weight_to_kg(value, unit, expected):
    got, canonical = normalize("weight", value, unit)
    assert canonical == "kg"
    assert got == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize("value,unit,expected", [
    (180, "cm", 180),
    (1.8, "m", 180),
    (70, "in", 177.8),
])
def test_height_to_cm(value, unit, expected):
    got, canonical = normalize("height", value, unit)
    assert canonical == "cm"
    assert got == pytest.approx(expected, rel=1e-3)


def test_heart_rate_count_per_min():
    assert normalize("heart_rate", 62, "count/min") == (62, "bpm")


def test_blood_pressure_dict_passes_through():
    value = {"systolic": 120, "diastolic": 80}
    got, unit = normalize("blood_pressure", value, "mmHg")
    assert got is value
    assert unit == "mmhg"


def test_missing_unit_is_left_alone():
    # No unit means no safe conversion — return the value untouched.
    assert normalize("glucose", 5.4, None) == (5.4, "mmol/l")


def test_unknown_unit_is_not_guessed():
    got, unit = normalize("glucose", 42, "furlongs")
    assert got == 42
    assert unit == "furlongs"


def test_unknown_metric_passes_through():
    got, unit = normalize("some_new_marker", 1.23, "ng/ml")
    assert got == 1.23
    assert unit == "ng/ml"
