"""Blood pressure staging must be bounded by both values.

The stage checks used `or`, so 200/85 satisfied "d < 90" and was reported as
stage 1 hypertension rather than a crisis.
"""
import pytest

from app.analysis.trend_analyzer import TrendAnalyzer


@pytest.fixture
def analyzer():
    return TrendAnalyzer.__new__(TrendAnalyzer)


@pytest.mark.parametrize("systolic,diastolic,expected", [
    (115, 75, "normal"),
    (125, 78, "elevated"),
    (135, 85, "hypertension_stage_1"),
    (150, 95, "hypertension_stage_2"),
    (200, 85, "hypertension_crisis"),   # was misreported as stage 1
    (185, 70, "hypertension_crisis"),
    (120, 125, "hypertension_crisis"),  # diastolic alone can be a crisis
])
def test_staging(analyzer, systolic, diastolic, expected):
    assert analyzer._interpret_blood_pressure([systolic], [diastolic]) == expected


def test_no_data(analyzer):
    assert analyzer._interpret_blood_pressure([], []) == "no_data"
