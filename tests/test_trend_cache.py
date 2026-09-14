"""New records must appear in /trends without waiting for a restart, and one
patient's cached data must never leak into another patient's request.

The TTL check lived in __init__ while the router held one module-level
analyzer, so self.data was frozen for the life of the process. Writes also
only invalidated the cache from the document-upload path. The cache is now a
dict keyed by patient_id (see app/analysis/trend_analyzer.py) rather than a
single shared slot, since there is more than one patient now.
"""
import pandas as pd
import pytest

from app.analysis.trend_analyzer import TrendAnalyzer

PATIENT_A = 1
PATIENT_B = 2


@pytest.fixture(autouse=True)
def clear_cache():
    TrendAnalyzer.invalidate_cache()
    yield
    TrendAnalyzer.invalidate_cache()


def test_refresh_picks_up_new_data(monkeypatch):
    rows = []
    analyzer = TrendAnalyzer.__new__(TrendAnalyzer)
    analyzer.patient_id = PATIENT_A
    monkeypatch.setattr(analyzer, "_load_data", lambda: pd.DataFrame(rows))

    analyzer.refresh()
    assert analyzer.data.empty

    rows.append({"metric": "glucose", "value": 5.2, "date": pd.Timestamp("2024-01-01")})
    TrendAnalyzer.invalidate_cache(PATIENT_A)
    analyzer.refresh()

    assert len(analyzer.data) == 1


def test_invalidate_cache_clears_one_patient_without_touching_others():
    TrendAnalyzer._data_cache[PATIENT_A] = pd.DataFrame([{"metric": "x"}])
    TrendAnalyzer._data_cache[PATIENT_B] = pd.DataFrame([{"metric": "y"}])
    TrendAnalyzer._cache_timestamp[PATIENT_A] = pd.Timestamp.now().to_pydatetime()
    TrendAnalyzer._cache_timestamp[PATIENT_B] = pd.Timestamp.now().to_pydatetime()

    TrendAnalyzer.invalidate_cache(PATIENT_A)

    assert PATIENT_A not in TrendAnalyzer._data_cache
    assert PATIENT_A not in TrendAnalyzer._cache_timestamp
    # Clearing one patient's cache must not evict another's.
    assert PATIENT_B in TrendAnalyzer._data_cache
    assert PATIENT_B in TrendAnalyzer._cache_timestamp


def test_invalidate_cache_with_no_patient_clears_everyone():
    TrendAnalyzer._data_cache[PATIENT_A] = pd.DataFrame([{"metric": "x"}])
    TrendAnalyzer._data_cache[PATIENT_B] = pd.DataFrame([{"metric": "y"}])
    TrendAnalyzer._cache_timestamp[PATIENT_A] = pd.Timestamp.now().to_pydatetime()
    TrendAnalyzer._cache_timestamp[PATIENT_B] = pd.Timestamp.now().to_pydatetime()

    TrendAnalyzer.invalidate_cache()

    assert TrendAnalyzer._data_cache == {}
    assert TrendAnalyzer._cache_timestamp == {}


def test_two_patients_never_share_a_cached_frame(monkeypatch):
    """The whole point of keying by patient_id: A's data must never answer B's request."""
    frames = {PATIENT_A: pd.DataFrame([{"metric": "a-only"}]),
              PATIENT_B: pd.DataFrame([{"metric": "b-only"}])}

    def fake_load(self):
        return frames[self.patient_id]

    monkeypatch.setattr(TrendAnalyzer, "_load_data", fake_load)

    analyzer_a = TrendAnalyzer(PATIENT_A)
    analyzer_b = TrendAnalyzer(PATIENT_B)

    assert analyzer_a.data["metric"].tolist() == ["a-only"]
    assert analyzer_b.data["metric"].tolist() == ["b-only"]


def test_analyze_trends_refreshes(monkeypatch):
    analyzer = TrendAnalyzer.__new__(TrendAnalyzer)
    analyzer.patient_id = PATIENT_A
    calls = []

    def fake_refresh():
        calls.append(1)
        analyzer.data = pd.DataFrame()

    monkeypatch.setattr(analyzer, "refresh", fake_refresh)
    analyzer.analyze_trends()

    assert calls, "analyze_trends must refresh before reading self.data"
