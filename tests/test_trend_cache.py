"""New records must appear in /trends without waiting for a restart.

The TTL check lived in __init__ while the router held one module-level
analyzer, so self.data was frozen for the life of the process. Writes also
only invalidated the cache from the document-upload path.
"""
import pandas as pd
import pytest

from app.analysis.trend_analyzer import TrendAnalyzer


@pytest.fixture(autouse=True)
def clear_cache():
    TrendAnalyzer.invalidate_cache()
    yield
    TrendAnalyzer.invalidate_cache()


def test_refresh_picks_up_new_data(monkeypatch):
    rows = []
    analyzer = TrendAnalyzer.__new__(TrendAnalyzer)
    monkeypatch.setattr(analyzer, "_load_data", lambda: pd.DataFrame(rows))

    analyzer.refresh()
    assert analyzer.data.empty

    rows.append({"metric": "glucose", "value": 5.2, "date": pd.Timestamp("2024-01-01")})
    TrendAnalyzer.invalidate_cache()
    analyzer.refresh()

    assert len(analyzer.data) == 1


def test_invalidate_cache_clears_shared_state():
    TrendAnalyzer._data_cache = pd.DataFrame([{"metric": "x"}])
    TrendAnalyzer._cache_timestamp = pd.Timestamp.now().to_pydatetime()

    TrendAnalyzer.invalidate_cache()

    assert TrendAnalyzer._data_cache is None
    assert TrendAnalyzer._cache_timestamp is None


def test_analyze_trends_refreshes(monkeypatch):
    analyzer = TrendAnalyzer.__new__(TrendAnalyzer)
    calls = []

    def fake_refresh():
        calls.append(1)
        analyzer.data = pd.DataFrame()

    monkeypatch.setattr(analyzer, "refresh", fake_refresh)
    analyzer.analyze_trends()

    assert calls, "analyze_trends must refresh before reading self.data"
