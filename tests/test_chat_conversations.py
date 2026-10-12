"""Chat conversations, and the chat seeing activity, sleep and the food diary.

Every question used to land in one endless thread, and the assistant could not
see the food diary or Apple Health steps and sleep at all.
"""
import json
import uuid
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.analysis.chat_context import build_health_context, format_health_context
from app.api import chat as chat_api
from app.chat_tools import run_tool
from app.config import settings
from app.database import AppleHealthData, ChatMessage, NutritionEntry, get_session
from app.main import app


@pytest.fixture
def model_calls(monkeypatch):
    calls = []

    def fake_ask(client, system_prompt, user_prompt, history=None, patient_id=None):
        calls.append({"prompt": user_prompt, "history": list(history or [])})
        return f"odpoveď {len(calls)}"

    monkeypatch.setattr(settings, "PROXY_SHARED_SECRET", "")
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(chat_api, "_ask_claude", fake_ask)
    return calls


def _client():
    c = TestClient(app)
    r = c.post("/api/auth/register", json={
        "email": f"conv-{uuid.uuid4().hex[:8]}@example.com",
        "password": "conv-password-1", "gdpr_consent": True,
    })
    assert r.status_code == 201
    return c, r.json()["patient_id"]


def test_a_question_without_a_conversation_starts_a_new_one(model_calls):
    c, _ = _client()
    first = c.post("/api/chat/ask", json={"question": "Aký mám tlak?"}).json()
    second = c.post("/api/chat/ask", json={"question": "Ako spím?"}).json()
    assert first["conversation_id"] != second["conversation_id"]
    titles = [conv["title"] for conv in c.get("/api/chat/conversations").json()["conversations"]]
    assert titles == ["Ako spím?", "Aký mám tlak?"]  # newest first


def test_a_follow_up_replays_only_its_own_conversation(model_calls):
    c, _ = _client()
    a = c.post("/api/chat/ask", json={"question": "Otázka A"}).json()["conversation_id"]
    c.post("/api/chat/ask", json={"question": "Otázka B"})
    c.post("/api/chat/ask", json={"question": "A ďalej?", "conversation_id": a})
    replayed = [m["content"] for m in model_calls[-1]["history"]]
    assert replayed == ["Otázka A", "odpoveď 1"]

    messages = c.get("/api/chat/history", params={"conversation_id": a}).json()["messages"]
    assert [m["content"] for m in messages] == ["Otázka A", "odpoveď 1", "A ďalej?", "odpoveď 3"]


def test_messages_from_before_conversations_are_grouped_by_day(model_calls):
    c, patient_id = _client()
    old = datetime(2026, 9, 1, 10, 0)
    session = get_session()
    session.add_all([
        ChatMessage(patient_id=patient_id, role="user", content="Stará otázka", created_at=old),
        ChatMessage(patient_id=patient_id, role="assistant", content="Stará odpoveď", created_at=old),
    ])
    session.commit()
    session.close()

    convs = c.get("/api/chat/conversations").json()["conversations"]
    assert [(x["id"], x["title"]) for x in convs] == [("legacy-2026-09-01", "Stará otázka")]
    c.post("/api/chat/ask", json={"question": "Pokračujem", "conversation_id": "legacy-2026-09-01"})
    assert [m["content"] for m in model_calls[-1]["history"]] == ["Stará otázka", "Stará odpoveď"]


def test_a_conversation_can_be_deleted_but_not_someone_elses(model_calls):
    owner, _ = _client()
    conv = owner.post("/api/chat/ask", json={"question": "Moja otázka"}).json()["conversation_id"]
    stranger, _ = _client()
    assert stranger.delete(f"/api/chat/conversations/{conv}").status_code == 404
    assert stranger.get("/api/chat/history", params={"conversation_id": conv}).json()["messages"] == []
    assert owner.delete(f"/api/chat/conversations/{conv}").json() == {"deleted": 2}
    assert owner.get("/api/chat/conversations").json()["conversations"] == []


def test_a_malformed_conversation_id_is_rejected(model_calls):
    c, _ = _client()
    assert c.post("/api/chat/ask", json={"question": "x", "conversation_id": "' OR 1=1"}).status_code == 400


def _add_lifestyle_data(patient_id):
    today = date.today()
    session = get_session()
    for back in range(3):
        day = datetime.combine(today - timedelta(days=back), datetime.min.time())
        session.add(AppleHealthData(patient_id=patient_id, record_type="HKQuantityTypeIdentifierStepCount",
                                    value=4000, unit="count", start_date=day + timedelta(hours=9), end_date=day + timedelta(hours=10)))
        session.add(AppleHealthData(patient_id=patient_id, record_type="HKQuantityTypeIdentifierStepCount",
                                    value=2500, unit="count", start_date=day + timedelta(hours=17), end_date=day + timedelta(hours=18)))
        # Two overlapping sleep records (in bed + asleep) for one night: 23:00–07:00.
        night = day - timedelta(hours=1)
        for start_h, end_h in ((0, 8), (1, 7)):
            session.add(AppleHealthData(patient_id=patient_id, record_type="HKCategoryTypeIdentifierSleepAnalysis",
                                        value=None, unit="", start_date=night + timedelta(hours=start_h),
                                        end_date=night + timedelta(hours=end_h)))
    session.add(NutritionEntry(patient_id=patient_id, logged_at=datetime.combine(today, datetime.min.time()) + timedelta(hours=8),
                               items=[{"name": "ovsená kaša", "calories": 350}], total_calories=350,
                               total_protein_g=12, total_carbs_g=55, total_fat_g=8))
    session.add(NutritionEntry(patient_id=patient_id, logged_at=datetime(2025, 3, 3, 12, 0),
                               items=[{"name": "guláš", "calories": 700}], total_calories=700,
                               total_protein_g=35, total_carbs_g=60, total_fat_g=30))
    session.commit()
    session.close()


def test_the_chat_context_includes_activity_sleep_and_the_food_diary(model_calls):
    _, patient_id = _client()
    _add_lifestyle_data(patient_id)
    text = format_health_context(build_health_context(patient_id))
    assert "POHYB A SPÁNOK" in text and "6500" in text
    assert "VÝŽIVA" in text and "ovsená kaša" in text


def test_tools_reach_any_period_of_activity_and_nutrition(model_calls):
    _, patient_id = _client()
    _add_lifestyle_data(patient_id)
    steps = json.loads(run_tool("get_activity_history", {"kind": "steps"}, patient_id))
    assert steps["days_with_data"] == 3 and steps["average_per_day"] == 6500
    sleep = json.loads(run_tool("get_activity_history", {"kind": "sleep"}, patient_id))
    assert {p["value"] for p in sleep["points"]} == {8.0}  # overlapping stages counted once

    old = json.loads(run_tool("get_nutrition_history", {"start_date": "2025-03-01", "end_date": "2025-03-31"}, patient_id))
    assert [m["meal"] for m in old["meals"]] == ["guláš"]
    assert old["daily_totals"][0]["calories"] == 700


def test_tools_never_read_another_patients_data(model_calls):
    _, owner = _client()
    _add_lifestyle_data(owner)
    _, other = _client()
    assert json.loads(run_tool("get_nutrition_history", {"start_date": "2025-01-01"}, other))["meals"] == []
    assert json.loads(run_tool("get_activity_history", {"kind": "steps"}, other))["points"] == []
