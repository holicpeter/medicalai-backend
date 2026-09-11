"""Server-side assembly of the patient's full health context for the chat.

Why this exists: /api/chat/ask answered from whatever the browser put in the
request body, and the page that feeds it loads GET /api/analysis/latest — an
endpoint that did not exist until this change. That loader failed on every
open, so every question arrived with health_data = null and the assistant
truthfully replied that it had no data, while the database held hundreds of lab
records and tens of thousands of Apple Health rows. Assembling the context here
means an answer depends on the database, not on what the client remembered to
send.

Everything is read through the loaders the rest of the API already uses
(TrendAnalyzer's cached frame, HealthMetricsAnalyzer's scoring, the manual-entry
tables), so the chat cannot drift away from what the dashboard shows. The output
is deliberately bounded: tens of thousands of Apple Health rows are aggregated
per metric per day, never listed, because the whole context has to fit into one
prompt.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.analysis.health_metrics import HealthMetricsAnalyzer
from app.analysis.trend_analyzer import TrendAnalyzer
from app.database import FamilyMember, Patient, get_session
from app.rag import document_inventory, search as search_documents

logger = logging.getLogger(__name__)

# The recent window answers questions like "posledné 3 dni"; anything older is
# represented by latest values and trends rather than row-by-row history.
DEFAULT_RECENT_DAYS = 30

# Per metric, at most this many daily rows reach the prompt. A year of daily
# Apple Health aggregates for a dozen metrics would otherwise crowd out the
# labs and the family history, which carry more signal per token.
MAX_DAYS_PER_METRIC = 30

# Hard ceiling on the rendered context. Claude Haiku's window is far larger,
# but an unbounded prompt is how a cheap endpoint quietly becomes expensive.
MAX_CONTEXT_CHARS = 16000

# Retrieved passages per question. Few on purpose: the numbers are already in
# the context as aggregates, so the passages only have to carry the wording
# around them — a doctor's conclusion, a prescribed medication, a referral.
MAX_PASSAGES = 5

# Documents are listed by name and date regardless of what retrieval matched,
# so the assistant never claims a report does not exist when it simply did not
# match the query terms.
MAX_LISTED_DOCUMENTS = 25

# Status thresholds, the health score and the alert wording live in
# HealthMetricsAnalyzer and must not be copied here — duplicated thresholds are
# exactly how the dashboard and the chat start disagreeing about the same
# value. Its __init__ loads every row from the database, which this module has
# already done through TrendAnalyzer's cached frame, so the instance is built
# without running __init__ and only methods that work purely off their
# arguments are called on it.
_scorer = object.__new__(HealthMetricsAnalyzer)


def _split_value(metric: str, value: Any) -> List[Tuple[str, float]]:
    """Flatten one stored measurement into (metric, number) pairs.

    Blood pressure is stored as {'systolic': .., 'diastolic': ..} and has to be
    aggregated as two series; everything else is already a number.
    """
    if isinstance(value, dict):
        pairs = []
        for part in ("systolic", "diastolic"):
            raw = value.get(part)
            if isinstance(raw, (int, float)):
                pairs.append((f"{metric}_{part}", float(raw)))
        return pairs
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [(metric, float(value))]
    return []


def _rows_from_frame(df: pd.DataFrame) -> List[Tuple[date, str, float, Optional[str], Optional[str]]]:
    rows: List[Tuple[date, str, float, Optional[str], Optional[str]]] = []
    if df is None or df.empty:
        return rows

    for row in df.itertuples(index=False):
        raw_date = getattr(row, "date", None)
        if raw_date is None or (isinstance(raw_date, float) and pd.isna(raw_date)):
            continue
        try:
            if pd.isna(raw_date):
                continue
        except (TypeError, ValueError):
            pass
        day = raw_date.date() if hasattr(raw_date, "date") else raw_date
        if not isinstance(day, date):
            continue

        metric = getattr(row, "metric", None)
        if not metric:
            continue
        unit = getattr(row, "unit", None)
        source = getattr(row, "source", None) or "unknown"
        for name, number in _split_value(str(metric), getattr(row, "value", None)):
            rows.append((day, name, number, unit, source))
    return rows


def _measurements(recent_days: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Inventory of everything stored, plus daily aggregates for the window."""
    inventory: Dict[str, Any] = {"total_rows": 0, "by_source": {}, "by_metric": {}}
    recent: Dict[str, List[Dict[str, Any]]] = {}

    try:
        rows = _rows_from_frame(TrendAnalyzer().data)
    except Exception as e:  # a broken loader must not take the chat down
        logger.warning("chat context: cannot load measurements: %s", e)
        return inventory, recent

    if not rows:
        return inventory, recent

    inventory["total_rows"] = len(rows)
    per_metric: Dict[str, Dict[str, Any]] = {}
    per_source: Dict[str, Dict[str, Any]] = {}
    daily: Dict[str, Dict[date, List[float]]] = {}
    cutoff = date.today() - timedelta(days=recent_days)

    for day, metric, value, unit, source in rows:
        m = per_metric.setdefault(
            metric, {"count": 0, "first": day, "last": day, "latest_value": value, "unit": unit}
        )
        m["count"] += 1
        if day < m["first"]:
            m["first"] = day
        if day >= m["last"]:
            m["last"] = day
            m["latest_value"] = value
            m["unit"] = unit or m["unit"]

        s = per_source.setdefault(source, {"count": 0, "first": day, "last": day})
        s["count"] += 1
        s["first"] = min(s["first"], day)
        s["last"] = max(s["last"], day)

        if day >= cutoff:
            daily.setdefault(metric, {}).setdefault(day, []).append(value)

    inventory["by_metric"] = {
        name: {
            "count": data["count"],
            "first": data["first"].isoformat(),
            "last": data["last"].isoformat(),
            "latest_value": round(data["latest_value"], 2),
            "unit": data["unit"],
            "status": _status(name, data["latest_value"]),
        }
        for name, data in sorted(per_metric.items())
    }
    inventory["by_source"] = {
        name: {
            "count": data["count"],
            "first": data["first"].isoformat(),
            "last": data["last"].isoformat(),
        }
        for name, data in sorted(per_source.items())
    }

    for metric, by_day in daily.items():
        days = sorted(by_day.keys(), reverse=True)[:MAX_DAYS_PER_METRIC]
        recent[metric] = [
            {
                "date": d.isoformat(),
                "n": len(by_day[d]),
                "avg": round(sum(by_day[d]) / len(by_day[d]), 2),
                "min": round(min(by_day[d]), 2),
                "max": round(max(by_day[d]), 2),
            }
            for d in sorted(days)
        ]

    return inventory, recent


def _status(metric: str, value: Any) -> str:
    try:
        return _scorer._get_metric_status(metric, value)
    except Exception:
        return "unknown"


def _latest_assessment(inventory: Dict[str, Any]) -> Dict[str, Any]:
    """Health score and alerts, derived from the same latest values as above."""
    latest = {
        name: {"value": data["latest_value"], "date": data["last"], "status": data["status"]}
        for name, data in inventory.get("by_metric", {}).items()
    }
    if not latest:
        return {"health_score": None, "alerts": []}
    try:
        return {
            "health_score": _scorer._calculate_health_score(latest),
            "alerts": _scorer._generate_alerts(latest),
        }
    except Exception as e:
        logger.warning("chat context: cannot score metrics: %s", e)
        return {"health_score": None, "alerts": []}


def _trends() -> Dict[str, Any]:
    try:
        raw = TrendAnalyzer().analyze_trends() or {}
    except Exception as e:
        logger.warning("chat context: cannot analyze trends: %s", e)
        return {}

    trends: Dict[str, Any] = {}
    for metric, data in raw.items():
        if not isinstance(data, dict) or "error" in data:
            continue
        # values_over_time is the full series — the aggregates above already
        # cover the recent window, so keeping it here would only duplicate it
        # at a much higher token cost.
        trends[metric] = {k: v for k, v in data.items() if k != "values_over_time"}
    return trends


def _patient() -> Dict[str, Any]:
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            return {}
        info: Dict[str, Any] = {
            "gender": patient.gender,
            "blood_type": patient.blood_type,
            "height_cm": patient.height_cm,
        }
        if patient.date_of_birth:
            today = date.today()
            born = patient.date_of_birth
            info["age"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return {k: v for k, v in info.items() if v is not None}
    except Exception as e:
        logger.warning("chat context: cannot load patient: %s", e)
        return {}
    finally:
        session.close()


def _family() -> List[Dict[str, Any]]:
    session = get_session()
    try:
        patient = session.query(Patient).first()
        if not patient:
            return []
        members = session.query(FamilyMember).filter_by(patient_id=patient.id).all()
        family = []
        for member in members:
            entry = {
                "relationship": member.relationship_type,
                "gender": member.gender,
                "chronic_conditions": member.chronic_conditions or [],
                "genetic_conditions": member.genetic_conditions or [],
                "smoking": member.smoking,
                "alcohol": member.alcohol,
                "cause_of_death": member.cause_of_death,
                "notes": member.notes,
            }
            family.append({k: v for k, v in entry.items() if v not in (None, [], "")})
        return family
    except Exception as e:
        logger.warning("chat context: cannot load family history: %s", e)
        return []
    finally:
        session.close()


def _risks() -> Dict[str, Any]:
    # Imported lazily: the predictor pulls in scikit-learn and loads its own
    # view of the data, and a chat answer is still useful without it.
    try:
        from app.ml.risk_predictor import RiskPredictor

        risks = RiskPredictor().predict_risks() or {}
        return {
            "overall_risk_score": risks.get("overall_risk_score"),
            "high_risk_conditions": risks.get("high_risk_conditions", []),
            "measured_metrics": risks.get("measured_metrics", []),
            "data_complete": risks.get("data_complete"),
        }
    except Exception as e:
        logger.warning("chat context: cannot compute risks: %s", e)
        return {}


def _documents(question: Optional[str]) -> Dict[str, Any]:
    """What is on file, plus the passages that match this particular question.

    The structured metrics answer "koľko"; this answers "čo k tomu napísal
    lekár". Retrieval is only run when there is a question to run it against —
    a context built for any other purpose still gets the inventory.
    """
    try:
        inventory = document_inventory()[:MAX_LISTED_DOCUMENTS]
    except Exception as e:
        logger.warning("chat context: cannot list documents: %s", e)
        inventory = []

    passages: List[Dict[str, Any]] = []
    if question:
        try:
            passages = search_documents(question, limit=MAX_PASSAGES)
        except Exception as e:
            logger.warning("chat context: document search failed: %s", e)

    return {"inventory": inventory, "passages": passages}


def build_health_context(
    recent_days: int = DEFAULT_RECENT_DAYS,
    question: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything the assistant is allowed to reason from, in one dict.

    `question` is what retrieval runs against; without it the context still
    describes the numbers and lists the documents, just without passages.
    """
    inventory, recent = _measurements(recent_days)
    context: Dict[str, Any] = {
        "today": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recent_days": recent_days,
        "patient": _patient(),
        "inventory": inventory,
        "recent": recent,
        "assessment": _latest_assessment(inventory),
        "trends": _trends(),
        "family": _family(),
        "risks": _risks(),
        "documents": _documents(question),
    }
    return context


def _format_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def format_health_context(context: Dict[str, Any]) -> str:
    """Render the context as the text block that goes into the prompt."""
    inventory = context.get("inventory") or {}
    by_metric = inventory.get("by_metric") or {}
    documents = context.get("documents") or {}
    if not by_metric and not context.get("family") and not documents.get("inventory"):
        return ""

    parts: List[str] = [f"DNEŠNÝ DÁTUM: {context.get('today')}"]

    patient = context.get("patient") or {}
    if patient:
        bits = []
        if patient.get("age") is not None:
            bits.append(f"vek {patient['age']}")
        if patient.get("gender"):
            bits.append(f"pohlavie {patient['gender']}")
        if patient.get("height_cm"):
            bits.append(f"výška {_format_number(patient['height_cm'])} cm")
        if patient.get("blood_type"):
            bits.append(f"krvná skupina {patient['blood_type']}")
        if bits:
            parts.append("\n=== PACIENT ===\n" + ", ".join(bits))

    by_source = inventory.get("by_source") or {}
    if by_source:
        lines = [
            f"  - {name}: {data['count']} meraní ({data['first']} až {data['last']})"
            for name, data in by_source.items()
        ]
        parts.append(
            "\n=== ČO JE V DATABÁZE (spolu {total} meraní) ===\n{lines}".format(
                total=inventory.get("total_rows", 0), lines="\n".join(lines)
            )
        )

    if by_metric:
        lines = []
        for name, data in by_metric.items():
            unit = f" {data['unit']}" if data.get("unit") else ""
            lines.append(
                f"  - {name}: {_format_number(data['latest_value'])}{unit} "
                f"(k {data['last']}, stav: {data['status']}, meraní: {data['count']})"
            )
        parts.append("\n=== NAJNOVŠIA HODNOTA KAŽDEJ METRIKY ===\n" + "\n".join(lines))

    assessment = context.get("assessment") or {}
    if assessment.get("health_score") is not None:
        parts.append(f"\n=== CELKOVÉ ZDRAVOTNÉ SKÓRE ===\n  {assessment['health_score']}/100")
    alerts = assessment.get("alerts") or []
    if alerts:
        lines = [
            f"  - [{a.get('severity')}] {a.get('metric')}: {a.get('message')} "
            f"(hodnota: {_format_number(a.get('value'))})"
            for a in alerts
        ]
        parts.append("\n=== VAROVANIA ===\n" + "\n".join(lines))

    trends = context.get("trends") or {}
    if trends:
        lines = []
        for metric, data in sorted(trends.items()):
            if "interpretation" in data or "trend" in data:
                trend = data.get("trend", "n/a")
                interpretation = data.get("interpretation", "")
                lines.append(f"  - {metric}: trend {trend}. {interpretation}".rstrip())
        if lines:
            parts.append("\n=== TRENDY ===\n" + "\n".join(lines))

    family = context.get("family") or []
    if family:
        lines = []
        for member in family:
            bits = [str(member.get("relationship", "príbuzný"))]
            for key, label in (
                ("chronic_conditions", "chronické"),
                ("genetic_conditions", "genetické"),
            ):
                if member.get(key):
                    bits.append(f"{label}: {', '.join(str(c) for c in member[key])}")
            if member.get("smoking"):
                bits.append("fajčiar")
            if member.get("cause_of_death"):
                bits.append(f"príčina úmrtia: {member['cause_of_death']}")
            if member.get("notes"):
                bits.append(str(member["notes"]))
            lines.append("  - " + " | ".join(bits))
        parts.append("\n=== RODINNÁ ANAMNÉZA ===\n" + "\n".join(lines))

    risks = context.get("risks") or {}
    if risks.get("overall_risk_score") is not None or risks.get("high_risk_conditions"):
        lines = []
        if risks.get("overall_risk_score") is not None:
            lines.append(f"  - celkové rizikové skóre: {risks['overall_risk_score']}")
        if risks.get("high_risk_conditions"):
            lines.append(
                "  - zvýšené riziko: " + ", ".join(str(c) for c in risks["high_risk_conditions"])
            )
        if risks.get("data_complete") is False:
            lines.append("  - pozn.: rizikový model nemá kompletné vstupy, ide o orientačný odhad")
        parts.append("\n=== PREDIKCIA RIZÍK (ML model) ===\n" + "\n".join(lines))

    doc_inventory = documents.get("inventory") or []
    if doc_inventory:
        lines = []
        for document in doc_inventory:
            when = document.get("date") or (document.get("uploaded_at") or "")[:10] or "bez dátumu"
            note = "" if document.get("has_text") else " (text nie je uložený)"
            lines.append(f"  - {document.get('filename')} — {when}{note}")
        parts.append(
            f"\n=== NAHRANÉ LEKÁRSKE DOKUMENTY ({len(doc_inventory)}) ===\n" + "\n".join(lines)
        )

    passages = documents.get("passages") or []
    if passages:
        lines = []
        for passage in passages:
            when = f", {passage['date']}" if passage.get("date") else ""
            lines.append(f"\n[{passage.get('document')}{when}]\n{passage.get('text')}")
        parts.append(
            "\n=== RELEVANTNÉ ÚRYVKY Z DOKUMENTOV (vyhľadané k tejto otázke) ==="
            + "\n".join(lines)
        )

    # Daily aggregates go last, and deliberately so: they are by far the
    # bulkiest section (every metric × every day), and the cap below trims from
    # the end. Everything that must survive truncation — latest values, alerts,
    # trends, family history, the retrieved passages — is already above.
    recent = context.get("recent") or {}
    if recent:
        lines = []
        for metric in sorted(recent):
            lines.append(f"\n{metric.upper().replace('_', ' ')}:")
            for day in recent[metric]:
                if day["n"] > 1:
                    lines.append(
                        f"  - {day['date']}: priemer {_format_number(day['avg'])} "
                        f"(min {_format_number(day['min'])}, max {_format_number(day['max'])}, "
                        f"{day['n']} meraní)"
                    )
                else:
                    lines.append(f"  - {day['date']}: {_format_number(day['avg'])}")
        parts.append(
            f"\n=== MERANIA ZA POSLEDNÝCH {context.get('recent_days')} DNÍ "
            "(denné agregáty) ===" + "\n".join(lines)
        )
    else:
        parts.append(
            f"\n=== MERANIA ZA POSLEDNÝCH {context.get('recent_days')} DNÍ ===\n"
            "  Za toto obdobie nie sú žiadne merania. Najnovšie dostupné hodnoty "
            "a ich dátumy sú vyššie."
        )

    text = "\n".join(parts)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n[... kontext skrátený ...]"
    return text
