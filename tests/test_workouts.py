"""Tréningový denník: ukladanie cvikov, história, progres a izolácia účtov.

Ukladanie a progres sú bez AI, takže nesmú míňať kredity — klient má limit
na rozpoznanie stroja nastavený na 0. Rozpoznanie z fotky sa testuje so
stubom modelu.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import workouts as workouts_api
from app.config import settings
from app.main import app
from app.workouts.identifier import parse_identification
from app.workouts.progress import exercise_key, progress_summary, set_metrics


def _register() -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={
        "email": f"workout-{uuid.uuid4().hex[:8]}@example.com",
        "password": "workout-password-1", "gdpr_consent": True,
    })
    assert r.status_code == 201
    return c


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_WORKOUTS", 0)  # proves logging needs no credit
    return _register()


def _bench(day: date, weight: float, reps: int = 8, name: str = "Bench press"):
    return {
        "exercise": name,
        "sets": [{"reps": reps, "weight_kg": weight}, {"reps": reps, "weight_kg": weight}],
        "logged_at": f"{day.isoformat()}T18:00:00",
    }


def test_an_exercise_is_saved_and_listed_for_its_day(client):
    day = date.today() - timedelta(days=2)
    r = client.post("/api/workouts/entries", json=_bench(day, 60))
    assert r.status_code == 200
    body = r.json()
    assert body["best_weight_kg"] == 60 and body["volume_kg"] == 960 and body["total_reps"] == 16

    listed = client.get("/api/workouts/entries", params={"target_date": day.isoformat()}).json()
    assert [e["exercise"] for e in listed] == ["Bench press"]
    assert client.get("/api/workouts/entries").json() == []
    days = client.get("/api/workouts/days").json()
    assert days == [{"date": day.isoformat(), "exercises": 1, "volume_kg": 960.0, "duration_min": 0.0}]


def test_cardio_needs_no_sets(client):
    r = client.post("/api/workouts/entries", json={
        "exercise": "Beh", "category": "cardio", "duration_min": 30, "distance_km": 5,
    })
    assert r.status_code == 200
    assert r.json()["sets"] == []


def test_an_empty_or_invalid_entry_is_refused(client):
    assert client.post("/api/workouts/entries", json={"exercise": "Drep", "sets": []}).status_code == 422
    assert client.post("/api/workouts/entries", json={"exercise": "  ", "duration_min": 10}).status_code == 422
    future = (datetime.now() + timedelta(days=3)).isoformat(timespec="seconds")
    assert client.post("/api/workouts/entries", json={**_bench(date.today(), 50), "logged_at": future}).status_code == 422
    bad = _bench(date.today(), 50)
    bad["sets"][0]["weight_kg"] = -1
    assert client.post("/api/workouts/entries", json=bad).status_code == 422


def test_history_groups_spellings_and_keeps_the_last_sets(client):
    today = date.today()
    client.post("/api/workouts/entries", json=_bench(today - timedelta(days=7), 60))
    client.post("/api/workouts/entries", json=_bench(today, 65, name="bench  Press"))
    client.post("/api/workouts/entries", json={"exercise": "Drep", "sets": [{"reps": 5, "weight_kg": 80}],
                                               "logged_at": f"{(today - timedelta(days=3)).isoformat()}T18:00:00"})

    history = client.get("/api/workouts/exercises").json()
    assert [h["exercise"] for h in history] == ["bench Press", "Drep"]
    bench = history[0]
    assert bench["sessions"] == 2 and bench["best_weight_kg"] == 65
    assert bench["last_sets"] == [{"reps": 8, "weight_kg": 65.0}, {"reps": 8, "weight_kg": 65.0}]


def test_progress_compares_first_and_last_session(client):
    today = date.today()
    client.post("/api/workouts/entries", json=_bench(today - timedelta(days=14), 60))
    client.post("/api/workouts/entries", json=_bench(today - timedelta(days=7), 62.5))
    client.post("/api/workouts/entries", json=_bench(today, 65))

    r = client.get("/api/workouts/progress", params={"exercise": "BENCH PRESS"})
    assert r.status_code == 200
    body = r.json()
    assert [p["best_weight_kg"] for p in body["points"]] == [60, 62.5, 65]
    s = body["summary"]
    assert s["metric"] == "est_1rm_kg" and s["sessions"] == 3 and s["improved"] is True
    assert s["change_pct"] == pytest.approx(8.3, abs=0.1)

    assert client.get("/api/workouts/progress", params={"exercise": "Mŕtvy ťah"}).status_code == 404


def test_an_entry_can_be_edited_and_deleted(client):
    created = client.post("/api/workouts/entries", json=_bench(date.today(), 60)).json()
    edited = client.put(f"/api/workouts/entries/{created['id']}", json=_bench(date.today(), 70))
    assert edited.status_code == 200 and edited.json()["best_weight_kg"] == 70
    assert client.delete(f"/api/workouts/entries/{created['id']}").status_code == 200
    assert client.get("/api/workouts/entries").json() == []


def test_another_account_cannot_see_edit_or_delete_workouts(client, monkeypatch):
    created = client.post("/api/workouts/entries", json=_bench(date.today(), 60)).json()
    other = _register()
    assert other.get("/api/workouts/entries").json() == []
    assert other.get("/api/workouts/exercises").json() == []
    assert other.get("/api/workouts/progress", params={"exercise": "Bench press"}).status_code == 404
    assert other.put(f"/api/workouts/entries/{created['id']}", json=_bench(date.today(), 99)).status_code == 404
    assert other.delete(f"/api/workouts/entries/{created['id']}").status_code == 404
    assert client.get("/api/workouts/entries").json()[0]["best_weight_kg"] == 60


def test_machine_photo_is_identified_and_matched_to_history(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_DAILY_LIMIT_WORKOUTS", 5)
    client.post("/api/workouts/entries", json={"exercise": "Leg press", "sets": [{"reps": 10, "weight_kg": 120}]})
    seen = {}

    def fake_identify(image_bytes, media_type, known):
        seen["known"] = known
        return {"equipment": "Leg press stroj", "exercise": "leg press", "category": "strength",
                "muscle_groups": ["stehná"], "how_to": "…", "alternatives": [], "confidence": 0.9}

    monkeypatch.setattr(workouts_api.identifier, "identify", fake_identify)
    r = client.post("/api/workouts/identify", files={"file": ("stroj.jpg", b"\xff\xd8fake", "image/jpeg")})
    assert r.status_code == 200
    assert seen["known"] == ["Leg press"]
    assert r.json()["history"]["last_sets"] == [{"reps": 10, "weight_kg": 120.0}]


def test_machine_photo_needs_an_image_and_a_credit(client):
    r = client.post("/api/workouts/identify", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    r = client.post("/api/workouts/identify", files={"file": ("a.jpg", b"\xff\xd8fake", "image/jpeg")})
    assert r.status_code == 429  # AI_DAILY_LIMIT_WORKOUTS = 0


# Pure functions, no database.

def test_exercise_key_ignores_case_spaces_and_diacritics():
    assert exercise_key("  Bench   Press ") == exercise_key("bench press") == "bench press"
    assert exercise_key("Mŕtvy ťah") == "mrtvy tah"


def test_one_rep_max_ignores_high_rep_sets():
    assert set_metrics([{"reps": 20, "weight_kg": 40}])["est_1rm_kg"] is None
    assert set_metrics([{"reps": 1, "weight_kg": 100}])["est_1rm_kg"] == 100
    assert set_metrics([{"reps": 10, "weight_kg": 60}])["est_1rm_kg"] == 80


def test_faster_pace_counts_as_improvement():
    points = [
        {"date": "2026-11-01", "est_1rm_kg": None, "best_weight_kg": None, "distance_km": None,
         "pace_min_per_km": 6.0, "duration_min": 30, "total_reps": 0},
        {"date": "2026-11-08", "est_1rm_kg": None, "best_weight_kg": None, "distance_km": None,
         "pace_min_per_km": 5.5, "duration_min": 30, "total_reps": 0},
    ]
    s = progress_summary(points)
    assert s["metric"] == "pace_min_per_km" and s["improved"] is True and s["best"] == 5.5


def test_identification_output_is_validated():
    out = parse_identification('```json\n{"exercise": "Lat pulldown", "category": "weird", "confidence": 3}\n```')
    assert out["exercise"] == "Lat pulldown" and out["category"] == "strength" and out["confidence"] == 1.0
    with pytest.raises(ValueError):
        parse_identification("no json here")


# The chat assistant sees the training diary.

def _log_bench_series(client):
    today = date.today()
    for back, weight in ((14, 60), (7, 62.5), (0, 65)):
        assert client.post("/api/workouts/entries", json=_bench(today - timedelta(days=back), weight)).status_code == 200


def test_the_chat_context_includes_workouts_and_their_progress(client):
    from app.analysis.chat_context import build_health_context, format_health_context

    _log_bench_series(client)
    patient_id = client.get("/api/auth/me").json()["patient_id"]
    text = format_health_context(build_health_context(patient_id))
    assert "TRÉNINGY" in text
    assert "Bench press: 3 tréningy" in text and "+8.3 %" in text
    assert "2× 8 × 65 kg" in text


def test_the_workout_tool_returns_one_exercise_over_its_whole_history(client):
    import json

    from app.chat_tools import run_tool

    _log_bench_series(client)
    patient_id = client.get("/api/auth/me").json()["patient_id"]
    result = json.loads(run_tool("get_workout_history", {"exercise": "bench press"}, patient_id))
    assert [p["best_weight_kg"] for p in result["points"]] == [60, 62.5, 65]
    assert result["per_exercise"][0]["summary"]["improved"] is True

    other = _register().get("/api/auth/me").json()["patient_id"]
    assert json.loads(run_tool("get_workout_history", {}, other))["sessions"] == []
