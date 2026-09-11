"""Tools the assistant calls when the prompt does not already carry the answer.

The context is a snapshot of the recent window, so a question about a longer
period ("ako mi šiel LDL za dva roky") had to be answered from the latest value
and a trend label. These cover the history tool that replaced that guess, the
dispatcher, and the loop that keeps tool use bounded.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from app import chat_tools
from app.analysis import chat_context
from app.api import chat as chat_api

TODAY = date.today()


def _rows():
    rows = [
        {"date": TODAY - timedelta(days=offset), "metric": "weight",
         "value": 80.0 + (offset % 5), "unit": "kg", "source": "withings"}
        for offset in range(0, 800, 7)  # weekly weigh-ins over two years
    ]
    rows += [
        {"date": TODAY - timedelta(days=offset), "metric": "glucose",
         "value": 5.1, "unit": "mmol/L", "source": "manual"}
        for offset in (1, 2)
    ]
    return rows


@pytest.fixture(autouse=True)
def measurements(monkeypatch):
    frame = pd.DataFrame(_rows())
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    monkeypatch.setattr(
        chat_context, "TrendAnalyzer",
        lambda: SimpleNamespace(data=frame, analyze_trends=dict),
    )
    return frame


def test_history_lists_every_measurement_day_by_day():
    history = chat_context.metric_history("weight")

    assert history["granularity"] == "daily"
    assert history["measurements"] == len(range(0, 800, 7))
    periods = [point["period"] for point in history["points"]]
    assert periods == sorted(periods), "a series has to be chronological"


def test_a_long_range_collapses_to_months_instead_of_being_cut_off():
    """A truncated series would silently hide half the period it claims to cover."""
    history = chat_context.metric_history("weight", max_points=20)

    assert history["granularity"] == "monthly"
    assert len(history["points"]) <= 20
    assert all(len(point["period"]) == len("2026-09") for point in history["points"])


def test_dates_narrow_the_series():
    since = (TODAY - timedelta(days=30)).isoformat()

    history = chat_context.metric_history("weight", start_date=since)

    assert history["measurements"] < len(range(0, 800, 7))
    assert all(point["period"] >= since for point in history["points"])


def test_an_unknown_metric_says_what_does_exist():
    """Better than an empty result: the model can retry with a real name."""
    history = chat_context.metric_history("ldl")

    assert history["points"] == []
    assert {"weight", "glucose"} <= set(history["available_metrics"])


@pytest.mark.parametrize("metric", ["weight", "WEIGHT", " Weight "])
def test_metric_names_are_matched_loosely(metric):
    assert chat_context.metric_history(metric)["measurements"] > 0


def test_an_unparseable_date_is_ignored_rather_than_fatal():
    assert chat_context.metric_history("weight", start_date="minulý rok")[
        "measurements"] > 0


def test_history_tool_returns_json():
    payload = json.loads(chat_tools.run_tool("get_metric_history", {"metric": "weight"}))

    assert payload["points"]
    assert payload["metric"] == "weight"


def test_search_tool_clamps_the_limit_and_caps_the_text(monkeypatch):
    seen = {}

    def _search(query, limit=5):
        seen["limit"] = limit
        return [{"document": "kardio.pdf", "date": "2026-03-14", "chunk_index": 0,
                 "score": 1.2, "text": "Záver: " + "x" * 6000}]

    monkeypatch.setattr(chat_tools, "search_documents", _search)

    payload = json.loads(
        chat_tools.run_tool("search_documents", {"query": "kardiológ", "limit": 99}))

    assert seen["limit"] == 8
    assert len(payload["results"][0]["text"]) <= chat_tools.MAX_DOCUMENT_CHARS + 10


def test_an_unknown_tool_is_reported_not_raised():
    assert "Neznámy nástroj" in chat_tools.run_tool("drop_table", {})


def test_a_failing_tool_is_reported_not_raised(monkeypatch):
    """The model can say what it could not look up; a 500 loses the whole answer."""
    def _boom(payload):
        raise RuntimeError("nope")

    monkeypatch.setitem(chat_tools._HANDLERS, "boom", _boom)

    assert "Nástroj zlyhal" in chat_tools.run_tool("boom", {})


class _Block(dict):
    """Stands in for an SDK content block: attribute and mapping access."""

    def __init__(self, **fields):
        super().__init__(**fields)
        self.__dict__.update(fields)


def _tool_turn(name, payload):
    return SimpleNamespace(
        stop_reason="tool_use",
        content=[_Block(type="tool_use", id="tu_1", name=name, input=payload)],
    )


def _text_turn(text):
    return SimpleNamespace(stop_reason="end_turn", content=[_Block(type="text", text=text)])


class _ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def test_a_question_needing_no_lookup_costs_one_round_trip():
    client = _ScriptedClient([_text_turn("Priama odpoveď.")])

    assert chat_api._ask_claude(client, "system", "otázka") == "Priama odpoveď."
    assert len(client.calls) == 1


def test_a_tool_result_is_fed_back_and_the_answer_returned():
    client = _ScriptedClient([
        _tool_turn("get_metric_history", {"metric": "weight"}),
        _text_turn("Vaša váha je stabilná."),
    ])

    answer = chat_api._ask_claude(client, "system", "otázka")

    assert answer == "Vaša váha je stabilná."
    assert "tools" in client.calls[0]
    messages = client.calls[1]["messages"]
    assert messages[1]["role"] == "assistant"
    result = messages[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "tu_1"
    assert "points" in result["content"]


def test_the_tool_budget_ends_in_an_answer_not_an_empty_turn():
    """A tool_use turn carries no text; returning it would show a blank reply."""
    client = _ScriptedClient(
        [_tool_turn("search_documents", {"query": "x"})] * chat_tools.MAX_TOOL_ROUNDS
        + [_text_turn("Odpoveď z toho, čo mám.")]
    )

    answer = chat_api._ask_claude(client, "system", "otázka")

    assert answer == "Odpoveď z toho, čo mám."
    assert len(client.calls) == chat_tools.MAX_TOOL_ROUNDS + 1
    assert "tools" not in client.calls[-1], "the last turn has to withhold tools"


def test_an_empty_reply_never_reaches_the_patient():
    client = _ScriptedClient([_text_turn("   ")])

    assert chat_api._ask_claude(client, "system", "otázka").startswith("Prepáčte")
