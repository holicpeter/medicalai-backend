"""Core body temperature from the ScanWatch 2.

The watch does not send temperature through getmeas (types 12/71/73 are the
Thermo's); it comes only as the intraday series core_body_temperature. These
tests pin how that series is read, folded into daily values for the chat, and
refreshed before the chat answers. Withings itself is never called.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from app.database import User, WearableConnection, get_session
from app.integrations import connections, withings_connector, withings_sync
from app.integrations.withings_connector import (
    MAX_TEMPERATURE_DAYS,
    WithingsConnector,
    parse_core_temperature,
)


def test_parses_the_series_keyed_by_timestamp():
    body = {"series": {
        "1763600000": {"core_body_temperature": 36.71, "deviceid": "x"},
        "1763600060": {"core_body_temperature": "36.8"},
        "1763600120": {"heart_rate": 60},  # another field, no temperature
    }}
    assert parse_core_temperature(body) == {1763600000: 36.71, 1763600060: 36.8}


def test_parses_the_older_list_form():
    body = {"series": [{"timestamp": 1763600000, "core_body_temperature": 37.1}]}
    assert parse_core_temperature(body) == {1763600000: 37.1}


def test_drops_readings_no_body_could_have():
    body = {"series": {"1": {"core_body_temperature": 24.0},   # watch off the wrist
                       "2": {"core_body_temperature": 36.5},
                       "3": {"core_body_temperature": 55.0},
                       "4": {"core_body_temperature": None}}}
    assert parse_core_temperature(body) == {2: 36.5}


def test_empty_answer_is_no_temperature():
    assert parse_core_temperature({}) == {}
    assert parse_core_temperature({"series": []}) == {}


def _connector_without_db():
    c = WithingsConnector.__new__(WithingsConnector)
    c.user_id = 1
    return c


def test_asks_one_day_at_a_time_and_caps_the_range(monkeypatch):
    calls = []

    async def fake_call(self, path, action, **params):
        calls.append((path, action, params))
        start = params["startdate"]
        return {"series": {str(start + 3600): {"core_body_temperature": 36.6}}}

    monkeypatch.setattr(WithingsConnector, "_call", fake_call)
    out = asyncio.run(_connector_without_db().get_core_body_temperature(days=400))

    assert len(calls) == MAX_TEMPERATURE_DAYS
    path, action, params = calls[0]
    assert (path, action) == ("/v2/measure", "getintradayactivity")
    assert params["data_fields"] == "core_body_temperature"
    assert params["enddate"] - params["startdate"] == 86400  # Withings' 24 h limit
    assert len(out) == MAX_TEMPERATURE_DAYS
    assert [s["measured_at"] for s in out] == sorted(s["measured_at"] for s in out)
    assert out[0]["measured_at"].endswith("+00:00")


def test_one_failing_day_does_not_lose_the_others(monkeypatch):
    async def fake_call(self, path, action, **params):
        if not hasattr(fake_call, "failed"):
            fake_call.failed = True
            raise RuntimeError("Withings status=503")
        return {"series": {str(params["startdate"] + 60): {"core_body_temperature": 36.9}}}

    monkeypatch.setattr(WithingsConnector, "_call", fake_call)
    out = asyncio.run(_connector_without_db().get_core_body_temperature(days=3))
    assert len(out) == 2


class _FakeConnector:
    def __init__(self, temperature=None, temperature_error=None):
        self._temperature = temperature or []
        self._error = temperature_error

    async def get_measures(self, days):
        return [{"metric": "spo2", "value": 98.0, "measured_at": "2026-11-20T00:17:00+00:00"}]

    async def get_sleep(self, days):
        return []

    async def get_sleep_hrv(self, nights):
        return []

    async def get_core_body_temperature(self, days):
        if self._error:
            raise self._error
        return self._temperature


def test_sync_stores_the_daily_median_as_body_temperature(monkeypatch):
    samples = [
        {"measured_at": "2026-11-20T01:00:00+00:00", "value": 36.4},
        {"measured_at": "2026-11-20T02:00:00+00:00", "value": 36.6},
        {"measured_at": "2026-11-20T03:00:00+00:00", "value": 39.0},
        {"measured_at": "2026-11-19T03:00:00+00:00", "value": 36.5},
    ]
    monkeypatch.setattr(withings_sync, "get_withings_connector", lambda uid: _FakeConnector(samples))
    daily = asyncio.run(withings_sync.collect_daily_metrics(1, days=2, hrv_nights=1))

    by_key = {(m, d.isoformat()): v for (m, d), v in daily.items()}
    assert by_key[("body_temperature", "2026-11-20")] == 36.6
    assert by_key[("body_temperature", "2026-11-19")] == 36.5
    assert ("oxygen_saturation", "2026-11-20") in by_key


def test_sync_survives_missing_temperature(monkeypatch):
    # No ScanWatch 2, or the Withings app lacks the biomarker plan.
    monkeypatch.setattr(withings_sync, "get_withings_connector",
                        lambda uid: _FakeConnector(temperature_error=RuntimeError("status=2555")))
    daily = asyncio.run(withings_sync.collect_daily_metrics(1, days=2, hrv_nights=1))
    assert {m for m, _ in daily} == {"oxygen_saturation"}


# --- Fresh data before the chat answers -------------------------------------

def _new_user_id():
    session = get_session()
    try:
        user = User(email=f"temp-{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
        session.add(user)
        session.commit()
        return user.id
    finally:
        session.close()


def _connection_row(last_sync_at):
    user_id = _new_user_id()
    session = get_session()
    try:
        session.add(WearableConnection(user_id=user_id, provider="withings",
                                       refresh_token_enc="x", last_sync_at=last_sync_at,
                                       connected_at=datetime.now()))
        session.commit()
    finally:
        session.close()
    return user_id


def _track_syncs(monkeypatch):
    calls = []

    async def fake_sync(user_id, days=30, hrv_nights=14):
        calls.append((user_id, days, hrv_nights))
        return {"written": 1}

    monkeypatch.setattr(connections, "sync_withings", fake_sync)
    connections._last_attempt.clear()
    return calls


def test_stale_data_is_refreshed_before_the_chat(monkeypatch):
    calls = _track_syncs(monkeypatch)
    user_id = _connection_row(datetime.now() - timedelta(hours=5))
    asyncio.run(connections.refresh_if_stale(user_id))
    assert calls == [(user_id, 2, 1)]


def test_fresh_data_is_left_alone(monkeypatch):
    calls = _track_syncs(monkeypatch)
    user_id = _connection_row(datetime.now() - timedelta(minutes=5))
    asyncio.run(connections.refresh_if_stale(user_id))
    assert calls == []


def test_a_failing_withings_is_not_retried_on_every_question(monkeypatch):
    calls = []

    async def failing_sync(user_id, days=30, hrv_nights=14):
        calls.append(user_id)
        raise RuntimeError("Withings down")

    monkeypatch.setattr(connections, "sync_withings", failing_sync)
    connections._last_attempt.clear()
    user_id = _connection_row(None)
    assert asyncio.run(connections.refresh_if_stale(user_id)) is None
    assert asyncio.run(connections.refresh_if_stale(user_id)) is None
    assert calls == [user_id]


def test_no_connection_means_no_sync(monkeypatch):
    calls = _track_syncs(monkeypatch)
    assert asyncio.run(connections.refresh_if_stale(_new_user_id())) is None
    assert calls == []
