"""Withings sync must bring in scale and blood pressure measurements.

collect_daily_metrics used to ask getmeas only for watch meastypes, so weight
and body fat from a Withings scale and readings from a Withings blood pressure
monitor never reached health_records, although the app advertises both.
"""
import asyncio

from app.integrations import withings_sync


class FakeConnector:
    def __init__(self, measures):
        self.measures = measures
        self.requested = None

    async def get_measures(self, days, meastypes=None):
        self.requested = meastypes
        return self.measures

    async def get_sleep(self, days):
        return []

    async def get_core_body_temperature(self, days):
        return []

    async def get_sleep_hrv(self, nights):
        return []


def _m(metric, value, at, device="dev1"):
    return {"metric": metric, "value": value, "measured_at": at, "device_id": device}


def _collect(monkeypatch, measures):
    fake = FakeConnector(measures)
    monkeypatch.setattr(withings_sync, "get_withings_connector", lambda user_id: fake)
    return fake, asyncio.run(withings_sync.collect_daily_metrics(user_id=1, days=30))


def test_requests_scale_and_bp_types(monkeypatch):
    fake, _ = _collect(monkeypatch, [])
    assert {1, 6, 9, 10, 11}.issubset(set(fake.requested))


def test_weight_and_body_fat_from_scale(monkeypatch):
    _, result = _collect(monkeypatch, [
        _m("weight", 80.4, "2026-11-10T07:00:00+00:00"),
        _m("fat_ratio", 21.5, "2026-11-10T07:00:00+00:00"),
    ])
    keys = {k[0] for k in result}
    assert "weight" in keys and "body_fat" in keys


def test_blood_pressure_pairs_into_one_reading_per_day(monkeypatch):
    _, result = _collect(monkeypatch, [
        _m("bp_systolic", 130, "2026-11-10T07:00:00+00:00"),
        _m("bp_diastolic", 85, "2026-11-10T07:00:00+00:00"),
        _m("bp_systolic", 120, "2026-11-10T19:00:00+00:00"),
        _m("bp_diastolic", 79, "2026-11-10T19:00:00+00:00"),
        # incomplete reading must be ignored
        _m("bp_systolic", 190, "2026-11-11T07:00:00+00:00"),
    ])
    bp = {day.isoformat(): v for (metric, day), v in result.items() if metric == "blood_pressure"}
    assert bp == {"2026-11-10": "125/82"}
