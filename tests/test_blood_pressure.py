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


def _bp_frame(rows):
    import pandas as pd
    return pd.DataFrame([
        {"metric": "blood_pressure", "value": v, "date": pd.Timestamp(d)} for d, v in rows
    ])


def test_trend_has_chart_points_for_both_values(analyzer):
    """The app showed "/" because the card had no top-level values and no
    history to chart. The analysis now returns one point per reading."""
    data = _bp_frame([
        ("2026-03-01", {"systolic": 141, "diastolic": 91}),
        ("2026-01-01", {"systolic": 135, "diastolic": 88}),
        ("2026-05-01", {"systolic": 126, "diastolic": 79}),
    ])
    result = analyzer._analyze_blood_pressure(data)

    assert result["count"] == 3
    assert [p["date"] for p in result["values_over_time"]] == ["2026-01-01", "2026-03-01", "2026-05-01"]
    assert result["values_over_time"][-1] == {"date": "2026-05-01", "systolic": 126.0, "diastolic": 79.0, "value": 126.0}
    assert result["systolic"]["latest"] == 126.0
    assert result["diastolic"]["latest"] == 79.0
    assert result["systolic"]["min"] == 126.0 and result["systolic"]["max"] == 141.0
    assert result["interpretation"] == "elevated"


def test_incomplete_readings_are_skipped(analyzer):
    data = _bp_frame([
        ("2026-01-01", {"systolic": 130, "diastolic": None}),
        ("2026-02-01", {"systolic": 120, "diastolic": 78}),
    ])
    result = analyzer._analyze_blood_pressure(data)
    assert result["count"] == 1
    assert len(result["values_over_time"]) == 1


def test_no_complete_readings(analyzer):
    data = _bp_frame([("2026-01-01", {"systolic": None, "diastolic": None})])
    assert "error" in analyzer._analyze_blood_pressure(data)
