"""Tool-calling tests: metric_history aggregation, run_tool, and the ask loop.

pandas and pydantic are real here; fastapi, anthropic and sqlalchemy are not
installable in this sandbox, so those and the DB/rag layers are stubbed. The
frames are built exactly as TrendAnalyzer builds them, so the aggregation is
exercised against real pandas rather than a hand-rolled fake.
"""
import json
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

# --- stub: analyzers, database, rag -----------------------------------------
hm = types.ModuleType("app.analysis.health_metrics")
hm.HealthMetricsAnalyzer = type("HealthMetricsAnalyzer", (), {
    "_get_metric_status": lambda self, m, v: "normal",
    "_calculate_health_score": lambda self, latest: 100,
    "_generate_alerts": lambda self, latest: [],
})
sys.modules["app.analysis.health_metrics"] = hm

ROWS = []
today = date.today()
for offset in range(0, 800, 7):  # ~114 weekly weigh-ins over two years
    ROWS.append({"date": today - timedelta(days=offset), "metric": "weight",
                 "value": 80.0 + (offset % 5), "unit": "kg", "source": "withings"})
for offset in (1, 2):
    ROWS.append({"date": today - timedelta(days=offset), "metric": "glucose",
                 "value": 5.1, "unit": "mmol/L", "source": "manual"})

ta = types.ModuleType("app.analysis.trend_analyzer")
ta.TrendAnalyzer = type("TrendAnalyzer", (), {
    "__init__": lambda self: setattr(self, "data", _frame(ROWS)),
    "analyze_trends": lambda self: {},
})
sys.modules["app.analysis.trend_analyzer"] = ta

db = types.ModuleType("app.database")
db.Patient = type("Patient", (), {})
db.FamilyMember = type("FamilyMember", (), {})
db.get_session = lambda: types.SimpleNamespace(
    query=lambda m: types.SimpleNamespace(first=lambda: None, filter_by=lambda **k: None),
    close=lambda: None,
)
sys.modules["app.database"] = db

SEARCH_CALLS = []
rag = types.ModuleType("app.rag")
rag.document_inventory = lambda: []
rag.search = lambda q, limit=5: (
    SEARCH_CALLS.append((q, limit)) or
    [{"document": "kardio.pdf", "date": "2026-03-14", "chunk_index": 0,
      "score": 1.2, "text": "Záver: " + "x" * 6000}]
)
sys.modules["app.rag"] = rag

# --- stub: fastapi / pydantic / anthropic -----------------------------------
fastapi = types.ModuleType("fastapi")
fastapi.APIRouter = lambda **kw: types.SimpleNamespace(post=lambda *a, **k: (lambda f: f))
fastapi.HTTPException = type("HTTPException", (Exception,), {
    "__init__": lambda self, status_code=500, detail="": (
        setattr(self, "status_code", status_code), setattr(self, "detail", detail), None)[-1]
})
sys.modules["fastapi"] = fastapi

anthropic_mod = types.ModuleType("anthropic")
anthropic_mod.Anthropic = object
sys.modules["anthropic"] = anthropic_mod

from app.analysis import chat_context  # noqa: E402
from app import chat_tools  # noqa: E402
from app.api import chat as chat_api  # noqa: E402

# --- metric_history ---------------------------------------------------------
weekly = chat_context.metric_history("weight")
assert weekly["measurements"] == len([r for r in ROWS if r["metric"] == "weight"])
assert weekly["granularity"] == "daily", weekly["granularity"]
assert weekly["points"][0]["period"] < weekly["points"][-1]["period"], "chronological"

# a long range collapses to months instead of returning a truncated series
monthly = chat_context.metric_history("weight", max_points=20)
assert monthly["granularity"] == "monthly", monthly["granularity"]
assert len(monthly["points"]) <= 20
assert all(len(p["period"]) == 7 for p in monthly["points"]), monthly["points"][:2]

# date filtering
recent = chat_context.metric_history(
    "weight", start_date=(today - timedelta(days=30)).isoformat())
assert recent["measurements"] < weekly["measurements"]
assert all(p["period"] >= (today - timedelta(days=30)).isoformat() for p in recent["points"])

# unknown metric: no data, but tell the model what does exist
unknown = chat_context.metric_history("ldl")
assert unknown["points"] == []
assert "weight" in unknown["available_metrics"] and "glucose" in unknown["available_metrics"]

# case-insensitive metric name, garbage dates ignored rather than fatal
assert chat_context.metric_history("WEIGHT")["measurements"] > 0
assert chat_context.metric_history("weight", start_date="minuly rok")["measurements"] > 0

# --- run_tool ---------------------------------------------------------------
payload = json.loads(chat_tools.run_tool("get_metric_history", {"metric": "weight"}))
assert payload["points"], payload

payload = json.loads(chat_tools.run_tool("search_documents", {"query": "kardiológ", "limit": 99}))
assert SEARCH_CALLS[-1][1] == 8, "limit must be clamped"
assert len(payload["results"][0]["text"]) <= chat_tools.MAX_DOCUMENT_CHARS + 10

assert "Neznámy nástroj" in chat_tools.run_tool("drop_table", {})
# a failing tool is reported, not raised
chat_tools._HANDLERS["boom"] = lambda p: (_ for _ in ()).throw(RuntimeError("nope"))
assert "Nástroj zlyhal" in chat_tools.run_tool("boom", {})
del chat_tools._HANDLERS["boom"]

# --- the ask loop -----------------------------------------------------------
class _Block(dict):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.__dict__.update(kw)


def _tool_turn(name, payload):
    return types.SimpleNamespace(
        stop_reason="tool_use",
        content=[_Block(type="tool_use", id="tu_1", name=name, input=payload)],
    )


def _text_turn(text):
    return types.SimpleNamespace(
        stop_reason="end_turn", content=[_Block(type="text", text=text)]
    )


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


# one lookup, then an answer
client = FakeClient([
    _tool_turn("get_metric_history", {"metric": "weight"}),
    _text_turn("Vaša váha je stabilná."),
])
answer = chat_api._ask_claude(client, "system", "otázka")
assert answer == "Vaša váha je stabilná.", answer
assert len(client.calls) == 2
assert "tools" in client.calls[0]
sent = client.calls[1]["messages"]
assert sent[1]["role"] == "assistant"
tool_result = sent[2]["content"][0]
assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
assert "points" in tool_result["content"]

# model keeps asking for tools: the budget is spent, then it must answer
client = FakeClient(
    [_tool_turn("search_documents", {"query": "x"})] * chat_tools.MAX_TOOL_ROUNDS
    + [_text_turn("Odpoveď z toho, čo mám.")]
)
answer = chat_api._ask_claude(client, "system", "otázka")
assert answer == "Odpoveď z toho, čo mám."
assert len(client.calls) == chat_tools.MAX_TOOL_ROUNDS + 1
assert "tools" not in client.calls[-1], "final turn must withhold tools"

# an empty reply never reaches the patient
client = FakeClient([_text_turn("   ")])
assert chat_api._ask_claude(client, "system", "otázka").startswith("Prepáčte")

# no tool call at all is the common path and costs one round trip
client = FakeClient([_text_turn("Priama odpoveď.")])
assert chat_api._ask_claude(client, "system", "otázka") == "Priama odpoveď."
assert len(client.calls) == 1

print("TOOLS: ALL ASSERTIONS PASSED")
print(f"  weight daily points: {len(weekly['points'])}, monthly: {len(monthly['points'])}")
print(f"  monthly sample: {monthly['points'][-1]}")
