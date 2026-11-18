"""Tréningový denník pre AI chat: prehľad do každého promptu a história pre nástroj.

Rovnaké rozdelenie ako pri jedálničku (app/analysis/lifestyle_data.py):
krátky prehľad ide do kontextu vždy, dlhšie obdobie alebo detail jedného
cviku si model vyžiada nástrojom get_workout_history. Čísla progresu počíta
app/workouts/progress.py, model ich len interpretuje.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app.database import WorkoutEntry, get_session
from app.workouts.progress import daily_points, exercise_key, progress_summary, set_metrics

logger = logging.getLogger(__name__)

OVERVIEW_DAYS = 14
DEFAULT_HISTORY_DAYS = 30
MAX_LISTED_DAYS = 60
MAX_OVERVIEW_EXERCISES = 20

METRIC_LABELS = {
    "est_1rm_kg": ("odhad 1RM", "kg"),
    "best_weight_kg": ("najvyššia váha", "kg"),
    "distance_km": ("vzdialenosť", "km"),
    "pace_min_per_km": ("tempo", "min/km"),
    "duration_min": ("trvanie", "min"),
    "total_reps": ("opakovania", ""),
}


def _parse(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _num(v: float) -> str:
    return f"{v:g}"


def sets_text(sets: Optional[List[dict]]) -> str:
    sets = [s for s in (sets or []) if s.get("reps")]
    if not sets:
        return ""
    first = sets[0]
    if all(s.get("reps") == first.get("reps") and s.get("weight_kg") == first.get("weight_kg") for s in sets):
        weight = f" × {_num(first['weight_kg'])} kg" if first.get("weight_kg") else ""
        return f"{len(sets)}× {first['reps']}{weight}"
    parts = [f"{s['reps']}×{_num(s['weight_kg'])}" if s.get("weight_kg") else str(s["reps"]) for s in sets]
    return ", ".join(parts) + (" kg" if any(s.get("weight_kg") for s in sets) else "")


def _entry_text(e: WorkoutEntry) -> str:
    bits = [sets_text(e.sets)]
    if e.duration_min:
        bits.append(f"{_num(e.duration_min)} min")
    if e.distance_km:
        bits.append(f"{_num(e.distance_km)} km")
    return f"{e.exercise} {' · '.join(b for b in bits if b)}".strip()


def summary_text(name: str, summary: Dict[str, Any], last_date: Optional[str] = None) -> str:
    """„Bench press: 3 tréningy, naposledy 2026-11-18; odhad 1RM 76 → 82.3 kg (+8.3 %)…"""
    n = summary["sessions"]
    text = f"{name}: {n} {'tréning' if n == 1 else 'tréningy' if 2 <= n <= 4 else 'tréningov'}"
    if last_date:
        text += f", naposledy {last_date}"
    metric = summary.get("metric")
    if metric:
        label, unit = METRIC_LABELS[metric]
        unit = f" {unit}" if unit else ""
        if summary["sessions"] > 1 and summary.get("change_pct") is not None:
            sign = "+" if summary["change_pct"] > 0 else ""
            text += (f"; {label} {_num(summary['first'])} → {_num(summary['last'])}{unit} "
                     f"({sign}{_num(summary['change_pct'])} %), najlepšie {_num(summary['best'])}{unit} ({summary['best_date']})")
        else:
            text += f"; {label} {_num(summary['last'])}{unit}"
    return text


def _query(session, patient_id: int, start: Optional[date], end: Optional[date], key: Optional[str] = None):
    q = session.query(WorkoutEntry).filter(WorkoutEntry.patient_id == patient_id)
    if start:
        q = q.filter(WorkoutEntry.logged_at >= datetime.combine(start, time.min))
    if end:
        q = q.filter(WorkoutEntry.logged_at < datetime.combine(end + timedelta(days=1), time.min))
    if key:
        q = q.filter(WorkoutEntry.exercise_key == key)
    return q.order_by(WorkoutEntry.logged_at, WorkoutEntry.id).all()


def _per_exercise(entries: List[WorkoutEntry]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[WorkoutEntry]] = {}
    for e in entries:
        groups.setdefault(e.exercise_key, []).append(e)
    out = []
    for group in groups.values():
        points = daily_points(group)
        out.append({
            "exercise": group[-1].exercise,
            "category": group[-1].category or "strength",
            "last_date": points[-1]["date"],
            "summary": progress_summary(points),
        })
    out.sort(key=lambda x: x["last_date"], reverse=True)
    return out


def _sessions(entries: List[WorkoutEntry]) -> List[Dict[str, Any]]:
    days: Dict[str, List[WorkoutEntry]] = {}
    for e in entries:
        days.setdefault(e.logged_at.date().isoformat(), []).append(e)
    return [
        {
            "date": d,
            "exercises": [
                {
                    "exercise": e.exercise,
                    "category": e.category or "strength",
                    "sets": sets_text(e.sets),
                    "duration_min": e.duration_min,
                    "distance_km": e.distance_km,
                    "volume_kg": set_metrics(e.sets)["volume_kg"],
                    **({"notes": e.notes} if e.notes else {}),
                }
                for e in group
            ],
        }
        for d, group in sorted(days.items())
    ]


def workout_history(
    patient_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    exercise: Optional[str] = None,
) -> Dict[str, Any]:
    """Tréningy za obdobie, alebo celý vývoj jedného cviku (vtedy bez obdobia celá história)."""
    end = _parse(end_date) or date.today()
    start = _parse(start_date)
    key = exercise_key(exercise) if exercise else None
    if start is None and key is None:
        start = end - timedelta(days=DEFAULT_HISTORY_DAYS - 1)
    if start and start > end:
        start, end = end, start

    session = get_session()
    try:
        entries = _query(session, patient_id, start, end, key)
    finally:
        session.close()

    sessions = _sessions(entries)
    result: Dict[str, Any] = {
        "start": start.isoformat() if start else (sessions[0]["date"] if sessions else None),
        "end": end.isoformat(),
        "training_days": len(sessions),
        "exercises_logged": len(entries),
        "per_exercise": _per_exercise(entries),
        "sessions": sessions[-MAX_LISTED_DAYS:],
    }
    if len(sessions) > MAX_LISTED_DAYS:
        result["sessions_note"] = f"Zobrazených posledných {MAX_LISTED_DAYS} z {len(sessions)} tréningových dní."
    if key:
        result["exercise"] = exercise
        result["points"] = daily_points(entries)
    if not entries:
        result["note"] = (f"Cvik „{exercise}“ nie je v tréningovom denníku." if key
                          else "Za toto obdobie nie sú v tréningovom denníku žiadne cviky.")
    return result


def workout_overview(patient_id: int, days: int = OVERVIEW_DAYS) -> Dict[str, Any]:
    """Progres každého cviku za celú históriu + tréningy za posledné dva týždne."""
    try:
        session = get_session()
        try:
            count, first, last = session.query(
                func.count(WorkoutEntry.id), func.min(WorkoutEntry.logged_at), func.max(WorkoutEntry.logged_at),
            ).filter(WorkoutEntry.patient_id == patient_id).one()
            if not count:
                return {"total_entries": 0}
            entries = _query(session, patient_id, None, None)
        finally:
            session.close()

        since = date.today() - timedelta(days=days - 1)
        recent = [e for e in entries if e.logged_at.date() >= since]
        return {
            "total_entries": count,
            "training_days": len({e.logged_at.date() for e in entries}),
            "first": first.date().isoformat() if first else None,
            "last": last.date().isoformat() if last else None,
            "recent_days": days,
            "per_exercise": _per_exercise(entries)[:MAX_OVERVIEW_EXERCISES],
            "recent": [
                {"date": s["date"], "text": "; ".join(_entry_text(e) for e in recent if e.logged_at.date().isoformat() == s["date"])}
                for s in _sessions(recent)
            ],
        }
    except Exception as e:
        logger.warning("chat context: workout overview failed: %s", e)
        return {}
