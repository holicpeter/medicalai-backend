"""The conversation is stored, and the last turns are replayed into the prompt.

Nothing about a chat used to survive the request. The answer went to the
browser, the browser held it in component state, and a refresh erased it — so
"a čo tie lieky?" reached the model with no idea what "tie" referred to, and
there was no record of what the app had told the patient.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api import chat as chat_api


class _Row:
    def __init__(self, role, content, created_at):
        self.role = role
        self.content = content
        self.created_at = created_at


class _Query:
    """Enough of the SQLAlchemy query chain for the two calls under test."""

    def __init__(self, rows, saved):
        self.rows = rows
        self.saved = saved
        self._limit = None

    def order_by(self, *args):
        return self

    def limit(self, count):
        self._limit = count
        return self

    def all(self):
        # newest first, as the query asks for
        ordered = sorted(self.rows, key=lambda r: r.created_at, reverse=True)
        return ordered[:self._limit] if self._limit else ordered

    def first(self):
        return SimpleNamespace(id=1)


class _Session:
    def __init__(self, rows, saved):
        self.rows = rows
        self.saved = saved
        self.committed = False

    def query(self, model):
        if model is chat_api.Patient:
            return _Query([], self.saved)
        return _Query(self.rows, self.saved)

    def add(self, row):
        self.saved.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def stored():
    base = datetime(2026, 9, 11, 14, 0, 0)
    return [
        _Row("user", "aké mám hodnoty cholesterolu?", base),
        _Row("assistant", "LDL 3,63 mmol/l k 19. 11. 2024.", base + timedelta(seconds=8)),
    ]


@pytest.fixture
def saved():
    return []


@pytest.fixture(autouse=True)
def session(monkeypatch, stored, saved):
    monkeypatch.setattr(chat_api, "get_session", lambda: _Session(stored, saved))


def test_history_comes_back_oldest_first_in_message_shape():
    history = chat_api.load_history()

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"].startswith("aké mám hodnoty")
    assert "created_at" not in history[0], "the replay carries no timestamps"


def test_the_history_endpoint_keeps_timestamps_and_the_whole_answer(stored):
    stored[1].content = "x" * 5000

    history = chat_api.load_history(limit=50, full=True)

    assert history[1]["content"] == "x" * 5000
    assert history[1]["created_at"] == "2026-09-11T14:00:08"


def test_a_long_answer_is_trimmed_before_it_is_replayed(stored):
    stored[1].content = "x" * 5000

    replayed = chat_api.load_history()[1]["content"]

    assert len(replayed) <= chat_api.MAX_REPLAYED_CHARS + 10
    assert replayed.endswith("[…]")


def test_only_the_last_turns_are_replayed(stored):
    base = datetime(2026, 9, 11, 12, 0, 0)
    for index in range(20):
        stored.append(_Row("user", f"otázka {index}", base + timedelta(minutes=index)))

    assert len(chat_api.load_history()) == chat_api.MAX_HISTORY_TURNS


def test_a_broken_database_costs_the_memory_not_the_answer(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_api, "get_session", _boom)

    assert chat_api.load_history() == []


def test_both_sides_of_the_turn_are_saved(saved):
    chat_api._save_turn("aké som mal operácie?", "Orchiopexia, 27. 1. 1993.")

    assert [row.role for row in saved] == ["user", "assistant"]
    assert saved[0].content == "aké som mal operácie?"
    assert saved[1].content == "Orchiopexia, 27. 1. 1993."


def test_earlier_turns_are_sent_ahead_of_the_question():
    """What makes a follow-up question resolvable at all."""
    class _Client:
        def __init__(self):
            self.calls = []
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="Odpoveď.")],
            )

    client = _Client()

    chat_api._ask_claude(client, "system", "a čo tie lieky?", chat_api.load_history())

    sent = client.calls[0]["messages"]
    assert len(sent) == 3
    assert sent[0]["content"].startswith("aké mám hodnoty")
    assert sent[-1]["content"] == "a čo tie lieky?"
