"""Read endpoints must keep their shape when the account has no data.

An empty database made every analysis endpoint return {"error": "..."} instead
of the documented structure. Callers that iterate the result (the dashboard)
got an object of a different shape and crashed.
"""
import pandas as pd
import pytest

from app.analysis.health_metrics import HealthMetricsAnalyzer
from app.analysis.trend_analyzer import TrendAnalyzer
from app.ml.risk_predictor import RiskPredictor


@pytest.fixture
def empty_metrics(monkeypatch):
    a = HealthMetricsAnalyzer.__new__(HealthMetricsAnalyzer)
    a.data = pd.DataFrame()
    monkeypatch.setattr(a, "_refresh", lambda: None)
    return a


def test_latest_metrics_is_empty_mapping(empty_metrics):
    result = empty_metrics.get_latest_metrics()
    assert result == {}
    assert "error" not in result


def test_metrics_history_is_empty_mapping(empty_metrics):
    assert empty_metrics.get_metrics_history() == {}


def test_summary_keeps_full_shape(empty_metrics):
    summary = empty_metrics.get_comprehensive_summary()
    for key in ("generated_at", "latest_metrics", "health_score", "alerts", "recommendations"):
        assert key in summary, f"missing {key}"
    assert summary["has_data"] is False
    assert summary["alerts"] == []
    assert "error" not in summary


def test_trends_is_empty_mapping():
    a = TrendAnalyzer.__new__(TrendAnalyzer)
    a.data = pd.DataFrame()
    result = a.analyze_trends()
    assert result == {}
    assert "error" not in result


def test_risks_keep_full_shape():
    p = RiskPredictor.__new__(RiskPredictor)
    p.data = pd.DataFrame()
    risks = p.predict_risks()
    for key in ("cardiovascular", "diabetes", "metabolic_syndrome",
                "overall_risk_score", "high_risk_conditions"):
        assert key in risks, f"missing {key}"
    assert risks["high_risk_conditions"] == []
    assert risks["has_data"] is False
    assert "error" not in risks
