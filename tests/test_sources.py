"""Every analyzer must read the same set of stored sources.

The dashboard analyzer loaded only manual/OCR records while the trend
analyzer also loaded Apple Health, so a completed Apple Health import left
the dashboard reporting zero metrics.
"""
import inspect

from app.analysis import health_metrics, trend_analyzer
from app.analysis.sources import APPLE_TO_METRIC, load_all_measurements


def test_both_analyzers_use_the_shared_loader():
    for module in (health_metrics, trend_analyzer):
        src = inspect.getsource(module)
        assert "load_all_measurements()" in src, (
            f"{module.__name__} must load measurements through the shared loader, "
            "otherwise the two can drift on which tables they read"
        )


def test_apple_health_mapping_covers_dashboard_metrics():
    # These are the metrics the dashboard interprets and that Apple Health
    # actually records; losing any of them silently empties a dashboard card.
    for metric in ("weight", "heart_rate", "bmi", "glucose"):
        assert metric in APPLE_TO_METRIC.values(), f"{metric} missing from Apple Health mapping"


def test_blood_pressure_is_mapped_from_both_halves():
    assert APPLE_TO_METRIC["HKQuantityTypeIdentifierBloodPressureSystolic"] == "blood_pressure_systolic"
    assert APPLE_TO_METRIC["HKQuantityTypeIdentifierBloodPressureDiastolic"] == "blood_pressure_diastolic"


def test_loader_returns_a_list_on_empty_database():
    assert isinstance(load_all_measurements(), list)
