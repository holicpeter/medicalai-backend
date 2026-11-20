"""Consensus: štúdie pre chat, bez údajov pacienta a bez pádu pri chybe služby."""
import json

import httpx
import pytest

from app import chat_tools
from app.api import chat as chat_api
from app.config import settings
from app.integrations import consensus

PATIENT_ID = 1


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "CONSENSUS_API_KEY", "test-key")
    consensus._cache.clear()
    yield
    consensus._cache.clear()


def _paper(**overrides):
    paper = {"title": "Creatine and memory", "publish_year": 2023, "journal_name": "Nutrients",
             "citation_count": 42, "takeaway": "Small benefit in older adults.", "doi": "10.1/abc"}
    paper.update(overrides)
    return paper


def test_without_a_key_the_tool_is_disabled_and_says_so(monkeypatch):
    monkeypatch.setattr(settings, "CONSENSUS_API_KEY", "  ")
    assert not consensus.is_enabled()
    assert "nie je nakonfigurované" in consensus.search_studies("creatine memory")["error"]
    assert "ŠTÚDIE" not in chat_api._studies_rule()


def test_studies_are_compacted_and_the_key_is_sent(monkeypatch):
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers)
        return _Response({"results": [_paper(), _paper(title="Second", doi=None, url="https://consensus.app/x")]})

    monkeypatch.setattr(consensus.httpx, "get", fake_get)
    result = consensus.search_studies("creatine memory", study_types=["rct", "made-up"], year_min="2015")

    assert seen["url"] == consensus.API_URL
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["params"]["study_types"] == "rct"
    assert seen["params"]["year_min"] == 2015
    assert result["studies"][0]["url"] == "https://doi.org/10.1/abc"
    assert result["studies"][1]["url"] == "https://consensus.app/x"
    assert result["studies"][0]["year"] == 2023


def test_long_queries_are_trimmed(monkeypatch):
    seen = {}
    monkeypatch.setattr(consensus.httpx, "get",
                        lambda url, params, headers, timeout: seen.update(params) or _Response({"results": []}))
    consensus.search_studies("x" * 1000)
    assert len(seen["query"]) == consensus.MAX_QUERY_CHARS


@pytest.mark.parametrize("status, message", [(429, "limit"), (401, "nedostupné"), (500, "nedostupné")])
def test_service_errors_come_back_as_an_error_not_an_exception(monkeypatch, status, message):
    monkeypatch.setattr(consensus.httpx, "get", lambda *a, **k: _Response({}, status=status))
    assert message in consensus.search_studies("creatine memory")["error"]


def test_network_failure_comes_back_as_an_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(consensus.httpx, "get", boom)
    assert "nedostupné" in consensus.search_studies("creatine memory")["error"]


def test_the_same_question_is_paid_for_once(monkeypatch):
    calls = []
    monkeypatch.setattr(consensus.httpx, "get",
                        lambda *a, **k: calls.append(1) or _Response({"results": [_paper()]}))
    consensus.search_studies("creatine memory")
    consensus.search_studies("creatine memory")
    assert len(calls) == 1


def test_run_tool_dispatches_without_passing_patient_data(monkeypatch):
    seen = {}
    monkeypatch.setattr(consensus.httpx, "get",
                        lambda url, params, headers, timeout: seen.update(params) or _Response({"results": [_paper()]}))
    payload = json.loads(chat_tools.run_tool("search_studies", {"query": "creatine memory"}, PATIENT_ID))
    assert payload["studies"][0]["title"] == "Creatine and memory"
    assert str(PATIENT_ID) not in json.dumps(seen)


def test_prompt_asks_for_studies_when_enabled():
    rule = chat_api._studies_rule()
    assert "search_studies" in rule
    assert "BEZ údajov pacienta" in rule
