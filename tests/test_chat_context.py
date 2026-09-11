"""The chat's context is built from the database, not from the request body.

The page feeding the chat posts whatever GET /api/analysis/latest returned, and
that endpoint did not exist, so every question used to arrive with
health_data = null and got "nemám žiadne údaje" as the honest answer to an
empty context — while the database held hundreds of records. These cover the
server-side assembly that replaced it: the daily roll-up, the recent window,
the "window is empty but data exists" branch that the original question hit,
blood-pressure splitting, and what actually reaches the prompt.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from app.analysis import chat_context

TODAY = date.today()

DOCUMENTS = [
    {"filename": "kardio-marec.pdf", "type": "lab_report", "date": "2026-03-14",
     "uploaded_at": "2026-03-15T10:00:00", "has_text": True},
    {"filename": "stary-nalez.pdf", "type": None, "date": None,
     "uploaded_at": "2025-11-02T08:30:00", "has_text": False},
]
PASSAGES = [{"document": "kardio-marec.pdf", "date": "2026-03-14", "chunk_index": 0,
             "score": 2.1, "text": "Záver kardiológa: ľahká hypertenzia, Prestarium 5 mg."}]


def _measurements():
    rows = []
    # many readings a day, as Apple Health delivers them
    for offset in (1, 2, 3):
        for value in (58.0, 62.0, 71.0, 88.0):
            rows.append({"date": TODAY - timedelta(days=offset), "metric": "heart_rate",
                         "value": value, "unit": "bpm", "source": "apple_health"})
    # a single lab value far outside the recent window
    rows.append({"date": TODAY - timedelta(days=400), "metric": "glucose",
                 "value": 7.4, "unit": "mmol/L", "source": "ocr"})
    # blood pressure is stored as a dict and has to become two series
    rows.append({"date": TODAY - timedelta(days=2), "metric": "blood_pressure",
                 "value": {"systolic": 128.0, "diastolic": 84.0}, "unit": "mmHg",
                 "source": "withings"})
    # an undated row must be skipped rather than crash the build
    rows.append({"date": None, "metric": "weight", "value": 80.0, "unit": "kg",
                 "source": "manual"})
    return rows


def _frame(rows):
    """The shape TrendAnalyzer._load_data produces."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame


class _Patient:
    gender = "M"
    blood_type = "A+"
    height_cm = 182.0
    date_of_birth = date(1985, 4, 12)
    id = 1


class _FamilyMember:
    relationship_type = "otec"
    gender = "M"
    chronic_conditions = ["hypertenzia", "diabetes 2. typu"]
    genetic_conditions = []
    allergies = []
    medications = []
    surgeries = []
    smoking = True
    alcohol = False
    cause_of_death = None
    notes = None


class _Query:
    def __init__(self, model):
        self.model = model

    def first(self):
        return _Patient() if self.model is chat_context.Patient else None

    def filter_by(self, **kwargs):
        return self

    def all(self):
        return [_FamilyMember()]


class _Session:
    def query(self, model):
        return _Query(model)

    def close(self):
        pass


@pytest.fixture
def rows():
    return _measurements()


@pytest.fixture(autouse=True)
def stub_sources(monkeypatch, rows):
    """Feed the builder fixtures instead of the database.

    Only the loaders are replaced. The scoring, aggregation and rendering under
    test stay real, including the thresholds shared with HealthMetricsAnalyzer.
    """
    class _TrendAnalyzer:
        def __init__(self):
            self.data = _frame(rows)

        def analyze_trends(self):
            return {
                "heart_rate": {
                    "trend": "stable",
                    "interpretation": "Pokojová frekvencia je v norme",
                    # the full series must never reach the prompt
                    "values_over_time": [{"date": "x", "value": 1}] * 500,
                },
                "glucose": {"error": "No numeric values"},
            }

    monkeypatch.setattr(chat_context, "TrendAnalyzer", _TrendAnalyzer)
    monkeypatch.setattr(chat_context, "get_session", lambda: _Session())
    monkeypatch.setattr(chat_context, "document_inventory", lambda: list(DOCUMENTS))
    monkeypatch.setattr(chat_context, "search_documents",
                        lambda q, limit=5: list(PASSAGES) if q else [])
    # the ML predictor loads its own view of the data; not what these cover
    monkeypatch.setattr(chat_context, "_risks", lambda: {})


@pytest.mark.parametrize("metric,value,expected", [
    ("blood_pressure", {"systolic": 120, "diastolic": 80},
     [("blood_pressure_systolic", 120.0), ("blood_pressure_diastolic", 80.0)]),
    ("weight", 80.5, [("weight", 80.5)]),
    ("weight", None, []),
    ("weight", "nezmerané", []),
    # bool is an int in Python; a flag is not a measurement
    ("flag", True, []),
])
def test_split_value(metric, value, expected):
    assert chat_context._split_value(metric, value) == expected


def test_inventory_counts_every_source_and_splits_blood_pressure():
    inventory = chat_context.build_health_context()["inventory"]

    # 12 heart rate + 1 glucose + blood pressure as two series; the undated row drops
    assert inventory["total_rows"] == 15
    assert set(inventory["by_source"]) == {"apple_health", "ocr", "withings"}
    assert "blood_pressure_systolic" in inventory["by_metric"]
    assert "blood_pressure_diastolic" in inventory["by_metric"]
    assert "weight" not in inventory["by_metric"]


def test_latest_value_carries_date_and_status():
    by_metric = chat_context.build_health_context()["inventory"]["by_metric"]

    glucose = by_metric["glucose"]
    assert glucose["latest_value"] == 7.4
    assert glucose["last"] == (TODAY - timedelta(days=400)).isoformat()
    # the threshold comes from HealthMetricsAnalyzer, not a copy living here
    assert glucose["status"] == "alert"


def test_recent_window_aggregates_per_day_and_excludes_older_values():
    recent = chat_context.build_health_context(recent_days=30)["recent"]

    assert len(recent["heart_rate"]) == 3
    day = recent["heart_rate"][0]
    assert day["n"] == 4
    assert day["min"] == 58 and day["max"] == 88
    assert day["avg"] == pytest.approx(69.75)
    assert "glucose" not in recent, "a 400-day-old lab value is not recent"


def test_trends_drop_errors_and_the_full_series():
    trends = chat_context.build_health_context()["trends"]

    assert "values_over_time" not in trends["heart_rate"]
    assert "glucose" not in trends, "entries carrying an error are not trends"


def test_patient_age_is_derived_from_date_of_birth():
    assert chat_context.build_health_context()["patient"]["age"] == (
        TODAY.year - 1985 - ((TODAY.month, TODAY.day) < (4, 12))
    )


def test_rendered_context_holds_what_the_model_needs():
    text = chat_context.format_health_context(chat_context.build_health_context())

    assert "DNEŠNÝ DÁTUM" in text
    assert "RODINNÁ ANAMNÉZA" in text and "hypertenzia" in text
    assert "priemer" in text
    assert len(text) <= chat_context.MAX_CONTEXT_CHARS


def test_empty_window_reports_the_latest_values_instead_of_no_data():
    """The exact case that produced "v systéme nie sú zaznamenané žiadne merania"."""
    text = chat_context.format_health_context(
        chat_context.build_health_context(recent_days=0))

    assert "Za toto obdobie nie sú žiadne merania" in text
    assert "NAJNOVŠIA HODNOTA KAŽDEJ METRIKY" in text
    assert "7.4" in text


def test_documents_are_listed_and_passages_retrieved_for_a_question():
    context = chat_context.build_health_context(question="čo písal kardiológ?")
    text = chat_context.format_health_context(context)

    assert context["documents"]["passages"], "a question must trigger retrieval"
    assert "NAHRANÉ LEKÁRSKE DOKUMENTY (2)" in text
    assert "stary-nalez.pdf" in text and "text nie je uložený" in text
    assert "Prestarium" in text


def test_passages_sit_above_the_section_the_size_cap_trims():
    text = chat_context.format_health_context(
        chat_context.build_health_context(question="kardiológ"))

    assert text.index("RELEVANTNÉ ÚRYVKY") < text.index("MERANIA ZA POSLEDNÝCH")


def test_without_a_question_there_is_an_inventory_but_no_retrieval():
    text = chat_context.format_health_context(chat_context.build_health_context())

    assert "NAHRANÉ LEKÁRSKE DOKUMENTY" in text
    assert "RELEVANTNÉ ÚRYVKY" not in text


def test_nothing_stored_renders_empty_so_the_caller_can_fall_back(monkeypatch, rows):
    """chat.py falls back to a client snapshot only when this is empty."""
    rows.clear()
    monkeypatch.setattr(chat_context, "document_inventory", list)
    monkeypatch.setattr(chat_context, "search_documents", lambda q, limit=5: [])
    monkeypatch.setattr(chat_context, "_family", list)

    assert chat_context.format_health_context(chat_context.build_health_context()) == ""
