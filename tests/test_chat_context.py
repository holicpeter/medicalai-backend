"""Exercise chat_context without sqlalchemy (not installable in this sandbox).

pandas is real; the analyzers, the DB layer and app.rag are stubbed. Covers the
daily roll-up, the recent window, the "window is empty but data exists" branch,
blood-pressure splitting and the rendered prompt.
"""
import sys
import types
from pathlib import Path
from collections import namedtuple
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --- real pandas (installed here), frames built like TrendAnalyzer builds them
import pandas as pd


def _frame(rows):
    """Same shape TrendAnalyzer._load_data produces: datetime64 dates, dict
    values left intact for blood pressure, undated rows dropped by the caller."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df

# --- stub: HealthMetricsAnalyzer (thresholds mirrored from the real one) -----
hm = types.ModuleType("app.analysis.health_metrics")


class HealthMetricsAnalyzer:
    def _get_metric_status(self, metric_name, value):
        thresholds = {"glucose": (5.6, 7.0), "bmi": (25, 30), "cholesterol": (5.2, 6.2)}
        if metric_name in thresholds and isinstance(value, (int, float)):
            warn, alert = thresholds[metric_name]
            if value >= alert:
                return "alert"
            if value >= warn:
                return "warning"
        return "normal"

    def _calculate_health_score(self, latest):
        score = 100
        for data in latest.values():
            if data.get("status") == "alert":
                score -= 15
            elif data.get("status") == "warning":
                score -= 5
        return max(0, min(100, score))

    def _generate_alerts(self, latest):
        out = []
        for name, data in latest.items():
            if data.get("status") == "alert":
                out.append({"severity": "high", "metric": name,
                            "message": f"{name} je výrazne nad normou", "value": data.get("value")})
        return out


hm.HealthMetricsAnalyzer = HealthMetricsAnalyzer
sys.modules["app.analysis.health_metrics"] = hm

# --- stub: TrendAnalyzer ----------------------------------------------------
ta = types.ModuleType("app.analysis.trend_analyzer")
ROWS = []
today = date.today()
# heart rate: many readings per day, recent
for offset in (1, 2, 3):
    for value in (58, 62, 71, 88):
        ROWS.append({"date": today - timedelta(days=offset), "metric": "heart_rate",
                     "value": float(value), "unit": "bpm", "source": "apple_health"})
# glucose: a single old lab value, far outside the recent window
ROWS.append({"date": today - timedelta(days=400), "metric": "glucose",
             "value": 7.4, "unit": "mmol/L", "source": "ocr"})
# blood pressure stored as a dict, recent
ROWS.append({"date": today - timedelta(days=2), "metric": "blood_pressure",
             "value": {"systolic": 128.0, "diastolic": 84.0}, "unit": "mmHg", "source": "withings"})
# a row with no usable date must be skipped, not crash
ROWS.append({"date": None, "metric": "weight", "value": 80.0, "unit": "kg", "source": "manual"})


class TrendAnalyzer:
    def __init__(self):
        self.data = _frame(ROWS)

    def analyze_trends(self):
        return {
            "heart_rate": {"trend": "stable", "interpretation": "Pokojová frekvencia je v norme",
                           "values_over_time": [{"date": "x", "value": 1}] * 500},
            "glucose": {"error": "No numeric values"},
        }


ta.TrendAnalyzer = TrendAnalyzer
sys.modules["app.analysis.trend_analyzer"] = ta

# --- stub: database ---------------------------------------------------------
db = types.ModuleType("app.database")


class Patient:
    pass


class FamilyMember:
    pass


class _Query:
    def __init__(self, model):
        self.model = model

    def first(self):
        if self.model is Patient:
            p = Patient()
            p.gender = "M"
            p.blood_type = "A+"
            p.height_cm = 182.0
            p.date_of_birth = date(1985, 4, 12)
            p.id = 1
            return p
        return None

    def filter_by(self, **kwargs):
        return self

    def all(self):
        m = FamilyMember()
        m.relationship_type = "otec"
        m.gender = "M"
        m.chronic_conditions = ["hypertenzia", "diabetes 2. typu"]
        m.genetic_conditions = []
        m.smoking = True
        m.alcohol = False
        m.cause_of_death = None
        m.notes = None
        return [m]


class _Session:
    def query(self, model):
        return _Query(model)

    def close(self):
        pass


db.Patient = Patient
db.FamilyMember = FamilyMember
db.get_session = lambda: _Session()
sys.modules["app.database"] = db


# --- stub: app.rag (retrieval is covered by test_rag.py) --------------------
rag = types.ModuleType("app.rag")
DOCS = [
    {"filename": "kardio-marec.pdf", "type": "lab_report", "date": "2026-03-14",
     "uploaded_at": "2026-03-15T10:00:00", "has_text": True},
    {"filename": "stary-nalez.pdf", "type": None, "date": None,
     "uploaded_at": "2025-11-02T08:30:00", "has_text": False},
]
PASSAGES = [{"document": "kardio-marec.pdf", "date": "2026-03-14", "chunk_index": 0,
             "score": 2.1, "text": "Záver kardiológa: ľahká hypertenzia, Prestarium 5 mg."}]
rag.document_inventory = lambda: list(DOCS)
rag.search = lambda q, limit=5: list(PASSAGES) if q else []
sys.modules["app.rag"] = rag

# --- run --------------------------------------------------------------------
from app.analysis import chat_context  # noqa: E402

assert chat_context._split_value("blood_pressure", {"systolic": 120, "diastolic": 80}) == [
    ("blood_pressure_systolic", 120.0), ("blood_pressure_diastolic", 80.0)]
assert chat_context._split_value("weight", 80.5) == [("weight", 80.5)]
assert chat_context._split_value("weight", None) == []
assert chat_context._split_value("flag", True) == []

ctx = chat_context.build_health_context(recent_days=30)
inv = ctx["inventory"]
assert inv["total_rows"] == 15, inv["total_rows"]  # 12 HR + 1 glucose + BP split in two
assert set(inv["by_source"]) == {"apple_health", "ocr", "withings"}, inv["by_source"]
assert inv["by_metric"]["glucose"]["status"] == "alert", inv["by_metric"]["glucose"]
assert "blood_pressure_systolic" in inv["by_metric"]
assert len(ctx["recent"]["heart_rate"]) == 3, ctx["recent"]["heart_rate"]
assert ctx["recent"]["heart_rate"][0]["n"] == 4
assert "glucose" not in ctx["recent"], "old lab value must not appear in the recent window"
assert "values_over_time" not in ctx["trends"]["heart_rate"]
assert "glucose" not in ctx["trends"], "error entries must be dropped"
assert ctx["patient"]["age"] >= 40

text = chat_context.format_health_context(ctx)
assert "DNEŠNÝ DÁTUM" in text and "RODINNÁ ANAMNÉZA" in text
assert "priemer" in text
assert len(text) <= chat_context.MAX_CONTEXT_CHARS

# the branch that matters for "posledné 3 dni": data exists, window is empty
narrow = chat_context.build_health_context(recent_days=0)
narrow_text = chat_context.format_health_context(narrow)
assert "Za toto obdobie nie sú žiadne merania" in narrow_text
assert "NAJNOVŠIA HODNOTA KAŽDEJ METRIKY" in narrow_text

# empty database renders as empty, so chat.py falls back to the client snapshot
ROWS.clear()
empty = chat_context.build_health_context()
empty["family"] = []
empty["documents"] = {"inventory": [], "passages": []}
assert chat_context.format_health_context(empty) == ""


# --- documents / RAG wiring -------------------------------------------------
with_docs = chat_context.build_health_context(recent_days=30, question="čo písal kardiológ?")
assert with_docs["documents"]["passages"], "a question must trigger retrieval"
doc_text = chat_context.format_health_context(with_docs)
assert "NAHRANÉ LEKÁRSKE DOKUMENTY (2)" in doc_text
assert "stary-nalez.pdf" in doc_text and "text nie je uložený" in doc_text
assert "RELEVANTNÉ ÚRYVKY" in doc_text and "Prestarium" in doc_text
# passages must sit above the bulky daily aggregates, so truncation cannot eat them
assert doc_text.index("RELEVANTNÉ ÚRYVKY") < doc_text.index("MERANIA ZA POSLEDNÝCH")

# no question -> inventory still there, no passages
no_q = chat_context.format_health_context(chat_context.build_health_context(recent_days=30))
assert "NAHRANÉ LEKÁRSKE DOKUMENTY" in no_q
assert "RELEVANTNÉ ÚRYVKY" not in no_q

print("ALL ASSERTIONS PASSED\n")
print("=" * 70)
print(text)
print("=" * 70)
print("\n--- narrow window (recent_days=0) tail ---")
print(narrow_text[narrow_text.index("=== MERANIA"):][:400])
