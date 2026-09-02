"""The risks screen must score the data the rest of the app already stores.

The predictor read only extracted_data_*.json off local disk, which is empty on
the deployed instance, so /predictions/risks answered zero for every disease no
matter what had been imported. It also loaded once at import time, defaulted
every missing metric to a healthy value, and ignored the split blood-pressure
metrics Apple Health writes.
"""
import pandas as pd
import pytest

from app.ml.risk_predictor import RiskPredictor


def _predictor(rows):
    p = RiskPredictor.__new__(RiskPredictor)
    p.data = pd.DataFrame(rows)
    return p


def _frozen(rows, monkeypatch):
    """A predictor whose refresh() is a no-op, so tests drive .data directly."""
    p = _predictor(rows)
    monkeypatch.setattr(p, "refresh", lambda: None)
    return p


def test_reads_database_backed_measurements(monkeypatch):
    rows = [
        {"metric": "glucose", "value": 8.1, "date": pd.Timestamp("2024-05-01")},
        {"metric": "hba1c", "value": 6.9, "date": pd.Timestamp("2024-05-01")},
        {"metric": "bmi", "value": 31.0, "date": pd.Timestamp("2024-05-01")},
    ]
    risks = _frozen(rows, monkeypatch).predict_risks()

    assert risks["has_data"] is True
    assert risks["diabetes"]["risk_level"] == "high"
    assert risks["overall_risk_score"] > 0
    assert "diabetes" in risks["high_risk_conditions"]


def test_latest_value_comes_from_the_date_not_row_order(monkeypatch):
    rows = [
        {"metric": "glucose", "value": 9.0, "date": pd.Timestamp("2020-01-01")},
        {"metric": "glucose", "value": 4.8, "date": pd.Timestamp("2024-01-01")},
    ]
    # Newest first, the order a "recent measurements" query returns.
    rows.reverse()
    features = _frozen(rows, monkeypatch)._prepare_features()

    assert features["glucose"] == 4.8


def test_split_blood_pressure_metrics_are_used(monkeypatch):
    rows = [
        {"metric": "blood_pressure_systolic", "value": 165.0, "date": pd.Timestamp("2024-01-01")},
        {"metric": "blood_pressure_diastolic", "value": 95.0, "date": pd.Timestamp("2024-01-01")},
    ]
    p = _frozen(rows, monkeypatch)

    assert p._prepare_features() == {"systolic": 165.0, "diastolic": 95.0}
    assert p.predict_disease_risk("hypertension")["risk_level"] == "high"


def test_combined_blood_pressure_dict_still_works(monkeypatch):
    rows = [{
        "metric": "blood_pressure",
        "value": {"systolic": 145.0, "diastolic": 92.0},
        "date": pd.Timestamp("2024-01-01"),
    }]
    assert _frozen(rows, monkeypatch).predict_disease_risk("hypertension")["risk_level"] == "high"


def test_bmi_is_derived_from_weight_and_height(monkeypatch):
    rows = [
        {"metric": "weight", "value": 100.0, "date": pd.Timestamp("2024-01-01")},
        {"metric": "height", "value": 180.0, "date": pd.Timestamp("2024-01-01")},
    ]
    features = _frozen(rows, monkeypatch)._prepare_features()

    assert features["bmi"] == pytest.approx(30.9, abs=0.1)


def test_missing_metrics_are_not_invented(monkeypatch):
    """A glucose-only account must not be scored as if its labs were normal."""
    rows = [{"metric": "glucose", "value": 4.8, "date": pd.Timestamp("2024-01-01")}]
    risks = _frozen(rows, monkeypatch).predict_risks()

    # Nothing the cardiovascular model needs was measured.
    cardio = risks["cardiovascular"]
    assert cardio["risk_level"] == "unknown"
    assert cardio["factors"] == []
    assert set(cardio["missing_metrics"]) == {"systolic", "ldl", "hdl", "bmi"}

    # Diabetes is scored on the one metric that exists, and says so.
    diabetes = risks["diabetes"]
    assert diabetes["evaluated_metrics"] == ["glucose"]
    assert set(diabetes["missing_metrics"]) == {"hba1c", "bmi"}

    assert risks["data_complete"] is False


def test_metrics_outside_the_models_do_not_count_as_data(monkeypatch):
    """Weight alone feeds no risk criterion, so nothing is scored from it."""
    rows = [{"metric": "weight", "value": 70.0, "date": pd.Timestamp("2024-01-01")}]
    risks = _frozen(rows, monkeypatch).predict_risks()

    assert risks["has_data"] is False
    assert risks["overall_risk_score"] == 0


def test_no_phantom_overweight_factor_without_a_measurement(monkeypatch):
    """The old BMI default of 25 reported "Nadváha" for everyone."""
    rows = [{"metric": "ldl", "value": 2.0, "date": pd.Timestamp("2024-01-01")}]
    cardio = _frozen(rows, monkeypatch).predict_risks()["cardiovascular"]

    assert cardio["factors"] == []
    assert "bmi" in cardio["missing_metrics"]


def test_disease_risk_without_data_keeps_its_shape(monkeypatch):
    p = _frozen([], monkeypatch)
    result = p.predict_disease_risk("cardiovascular")

    assert result["risk_level"] == "unknown"
    assert result["risk_percentage"] == 0
    assert result["data_complete"] is False
    assert "error" not in result


def test_refresh_picks_up_data_added_after_startup(monkeypatch):
    """The router holds one module-level predictor for the whole process."""
    class FakeAnalyzer:
        def __init__(self):
            self.data = pd.DataFrame()

        def refresh(self):
            self.data = pd.DataFrame(rows)

    rows = []
    p = RiskPredictor.__new__(RiskPredictor)
    p._analyzer = FakeAnalyzer()
    p.data = pd.DataFrame()

    assert p.predict_risks()["has_data"] is False

    rows.append({"metric": "glucose", "value": 6.0, "date": pd.Timestamp("2024-01-01")})
    assert p.predict_risks()["has_data"] is True
