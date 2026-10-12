"""Activity, sleep and nutrition for the chat assistant.

The measurement pipeline (app.analysis.sources) only carries the Apple Health
types that are clinical values — weight, heart rate, blood pressure and a few
more. Steps, sleep, calories burned, HRV or VO2max never reached the chat, and
neither did the food diary, so "koľko som minulý týždeň spal" or "jem dosť
bielkovín?" got an honest "nemám tieto údaje" although the app had them.

Both come in two sizes, the same way measurements do: a short overview that
goes into every prompt (what exists, the last week day by day), and a history
function the assistant can call as a tool for any period.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from app.database import AppleHealthData, NutritionEntry, get_session

logger = logging.getLogger(__name__)

# kind -> (Apple record type, how a day is aggregated, unit shown to the model)
ACTIVITY_KINDS: Dict[str, Tuple[str, str, str]] = {
    "steps": ("HKQuantityTypeIdentifierStepCount", "sum", "krokov"),
    "sleep": ("HKCategoryTypeIdentifierSleepAnalysis", "sleep", "hodín"),
    "active_energy": ("HKQuantityTypeIdentifierActiveEnergyBurned", "sum", "kcal"),
    "basal_energy": ("HKQuantityTypeIdentifierBasalEnergyBurned", "sum", "kcal"),
    "distance": ("HKQuantityTypeIdentifierDistanceWalkingRunning", "sum", "km"),
    "flights_climbed": ("HKQuantityTypeIdentifierFlightsClimbed", "sum", "poschodí"),
    "hrv": ("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "avg", "ms"),
    "vo2max": ("HKQuantityTypeIdentifierVO2Max", "avg", "ml/kg/min"),
    "respiratory_rate": ("HKQuantityTypeIdentifierRespiratoryRate", "avg", "dychov/min"),
    "walking_heart_rate": ("HKQuantityTypeIdentifierWalkingHeartRateAverage", "avg", "bpm"),
    "body_temperature": ("HKQuantityTypeIdentifierBodyTemperature", "avg", "°C"),
    "water": ("HKQuantityTypeIdentifierDietaryWater", "sum", "ml"),
    "lean_body_mass": ("HKQuantityTypeIdentifierLeanBodyMass", "avg", "kg"),
}
_KIND_BY_TYPE = {record_type: kind for kind, (record_type, _, _) in ACTIVITY_KINDS.items()}

# Shown every day in the prompt; the rest is listed and fetched on demand.
OVERVIEW_KINDS = ("steps", "sleep", "active_energy")
OVERVIEW_DAYS = 7

# A history for a long period is sent per week rather than per day, so a
# "za posledný rok" question costs ~52 points, not 365.
MAX_DAILY_POINTS = 92
MAX_LISTED_MEALS = 150


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _period(start_date: Optional[str], end_date: Optional[str], default_days: int) -> Tuple[date, date]:
    end = _parse_date(end_date) or date.today()
    start = _parse_date(start_date) or (end - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    return start, end


def _convert(kind: str, value: float, unit: Optional[str]) -> float:
    u = (unit or "").lower()
    if kind == "distance":
        if u == "m":
            return value / 1000
        if u == "mi":
            return value * 1.609344
    if kind in ("active_energy", "basal_energy") and u == "kj":
        return value / 4.184
    if kind == "water" and u in ("l", "liter"):
        return value * 1000
    if kind == "lean_body_mass" and u == "lb":
        return value * 0.45359237
    if kind == "body_temperature" and u in ("degf", "°f"):
        return (value - 32) * 5 / 9
    return value


def _sleep_hours_by_day(rows) -> Dict[date, float]:
    """Hours of sleep per night, credited to the morning it ended.

    Stages arrive as separate overlapping records (in bed, core, deep, REM…)
    and their kind is not kept on import, so summing them would count the
    same night several times. Merging the intervals gives the time covered.
    """
    by_day: Dict[date, List[Tuple[datetime, datetime]]] = defaultdict(list)
    for start, end in rows:
        if start and end and end > start:
            by_day[end.date()].append((start, end))
    hours: Dict[date, float] = {}
    for day, spans in by_day.items():
        spans.sort()
        total = timedelta()
        cur_start, cur_end = spans[0]
        for s, e in spans[1:]:
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                total += cur_end - cur_start
                cur_start, cur_end = s, e
        total += cur_end - cur_start
        hours[day] = round(total.total_seconds() / 3600, 1)
    return hours


def _daily_values(session, patient_id: int, kind: str, start: date, end: date) -> Dict[date, float]:
    record_type, how, _ = ACTIVITY_KINDS[kind]
    # Sleep that ends on `start` began the evening before.
    lower = datetime.combine(start - timedelta(days=1 if how == "sleep" else 0), time.min)
    upper = datetime.combine(end + timedelta(days=1), time.min)
    query = session.query(
        AppleHealthData.start_date, AppleHealthData.end_date, AppleHealthData.value, AppleHealthData.unit,
    ).filter(
        AppleHealthData.patient_id == patient_id,
        AppleHealthData.record_type == record_type,
        AppleHealthData.start_date >= lower,
        AppleHealthData.start_date < upper,
    )
    if how == "sleep":
        hours = _sleep_hours_by_day((r.start_date, r.end_date) for r in query)
        return {d: h for d, h in hours.items() if start <= d <= end}

    sums: Dict[date, float] = defaultdict(float)
    counts: Dict[date, int] = defaultdict(int)
    for r in query:
        if r.value is None or r.start_date is None:
            continue
        day = r.start_date.date()
        sums[day] += _convert(kind, float(r.value), r.unit)
        counts[day] += 1
    if how == "sum":
        return dict(sums)
    return {d: sums[d] / counts[d] for d in sums}


def _round(kind: str, value: float) -> float:
    return round(value) if kind in ("steps", "active_energy", "basal_energy", "water", "flights_climbed") else round(value, 1)


def activity_history(
    kind: str, patient_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """One activity/sleep series for any period: per day, or per week when long."""
    kind = str(kind or "").strip().lower()
    if kind not in ACTIVITY_KINDS:
        return {"error": f"Neznámy typ '{kind}'. Dostupné: {', '.join(ACTIVITY_KINDS)}"}
    start, end = _period(start_date, end_date, default_days=30)
    session = get_session()
    try:
        daily = _daily_values(session, patient_id, kind, start, end)
    finally:
        session.close()

    unit = ACTIVITY_KINDS[kind][2]
    if not daily:
        return {"kind": kind, "unit": unit, "start": start.isoformat(), "end": end.isoformat(),
                "points": [], "note": "Za toto obdobie nie sú žiadne záznamy."}

    days = sorted(daily)
    if (end - start).days + 1 <= MAX_DAILY_POINTS:
        granularity = "daily"
        points = [{"date": d.isoformat(), "value": _round(kind, daily[d])} for d in days]
    else:
        # Weekly average of the daily values (for steps: average steps per day that week).
        granularity = "weekly_average_per_day"
        weeks: Dict[date, List[float]] = defaultdict(list)
        for d in days:
            weeks[d - timedelta(days=d.weekday())].append(daily[d])
        points = [
            {"week_start": w.isoformat(), "value": _round(kind, sum(v) / len(v)), "days_with_data": len(v)}
            for w, v in sorted(weeks.items())
        ]
    values = [daily[d] for d in days]
    return {
        "kind": kind, "unit": unit, "granularity": granularity,
        "start": start.isoformat(), "end": end.isoformat(),
        "days_with_data": len(days),
        "average_per_day": _round(kind, sum(values) / len(values)),
        "min": _round(kind, min(values)), "max": _round(kind, max(values)),
        "points": points,
    }


def activity_overview(patient_id: int, days: int = OVERVIEW_DAYS) -> Dict[str, Any]:
    """Which activity data exist at all, and the last `days` of steps, sleep and calories."""
    session = get_session()
    try:
        rows = (
            session.query(
                AppleHealthData.record_type,
                func.count(AppleHealthData.id),
                func.min(AppleHealthData.start_date),
                func.max(AppleHealthData.start_date),
            )
            .filter(
                AppleHealthData.patient_id == patient_id,
                AppleHealthData.record_type.in_(list(_KIND_BY_TYPE)),
            )
            .group_by(AppleHealthData.record_type)
            .all()
        )
        available = {
            _KIND_BY_TYPE[record_type]: {
                "records": count,
                "first": first.date().isoformat() if first else None,
                "last": last.date().isoformat() if last else None,
            }
            for record_type, count, first, last in rows
        }
        end = date.today()
        start = end - timedelta(days=days - 1)
        recent = {}
        for kind in OVERVIEW_KINDS:
            if kind in available:
                daily = _daily_values(session, patient_id, kind, start, end)
                if daily:
                    recent[kind] = [(d.isoformat(), _round(kind, daily[d])) for d in sorted(daily)]
        return {"available": available, "recent": recent, "recent_days": days}
    except Exception as e:
        logger.warning("chat context: activity overview failed: %s", e)
        return {}
    finally:
        session.close()


def _meal_names(entry: NutritionEntry) -> str:
    items = entry.items if isinstance(entry.items, list) else []
    names = [str(i.get("name")) for i in items if isinstance(i, dict) and i.get("name")]
    return ", ".join(names) or "jedlo"


def nutrition_history(
    patient_id: int, start_date: Optional[str] = None, end_date: Optional[str] = None,
    include_meals: bool = True,
) -> Dict[str, Any]:
    """Food diary for any period: totals per day and, optionally, the meals themselves."""
    start, end = _period(start_date, end_date, default_days=7)
    session = get_session()
    try:
        entries = (
            session.query(NutritionEntry)
            .filter(
                NutritionEntry.patient_id == patient_id,
                NutritionEntry.logged_at >= datetime.combine(start, time.min),
                NutritionEntry.logged_at < datetime.combine(end + timedelta(days=1), time.min),
            )
            .order_by(NutritionEntry.logged_at)
            .all()
        )
    finally:
        session.close()

    days: Dict[str, Dict[str, float]] = {}
    meals = []
    for e in entries:
        key = e.logged_at.date().isoformat()
        day = days.setdefault(key, {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "meals": 0})
        day["calories"] += e.total_calories or 0
        day["protein_g"] += e.total_protein_g or 0
        day["carbs_g"] += e.total_carbs_g or 0
        day["fat_g"] += e.total_fat_g or 0
        day["meals"] += 1
        meals.append({
            "time": e.logged_at.isoformat(timespec="minutes"),
            "meal": _meal_names(e),
            "kcal": round(e.total_calories or 0),
            "protein_g": round(e.total_protein_g or 0, 1),
            "carbs_g": round(e.total_carbs_g or 0, 1),
            "fat_g": round(e.total_fat_g or 0, 1),
        })
    daily = [
        {"date": k, **{m: round(v, 1) if m != "meals" else int(v) for m, v in d.items()}}
        for k, d in sorted(days.items())
    ]
    result: Dict[str, Any] = {
        "start": start.isoformat(), "end": end.isoformat(),
        "days_with_meals": len(daily), "meals_logged": len(meals),
        "daily_totals": daily,
    }
    if daily:
        result["average_per_logged_day"] = {
            m: round(sum(d[m] for d in daily) / len(daily), 1)
            for m in ("calories", "protein_g", "carbs_g", "fat_g")
        }
    if include_meals:
        result["meals"] = meals[-MAX_LISTED_MEALS:]
        if len(meals) > MAX_LISTED_MEALS:
            result["meals_note"] = f"Zobrazených posledných {MAX_LISTED_MEALS} z {len(meals)} jedál."
    if not entries:
        result["note"] = "Za toto obdobie nie sú v jedálničku žiadne jedlá."
    return result


def nutrition_overview(patient_id: int, days: int = OVERVIEW_DAYS) -> Dict[str, Any]:
    """Last week of the food diary plus how far back it goes."""
    try:
        session = get_session()
        try:
            count, first, last = session.query(
                func.count(NutritionEntry.id), func.min(NutritionEntry.logged_at), func.max(NutritionEntry.logged_at),
            ).filter(NutritionEntry.patient_id == patient_id).one()
        finally:
            session.close()
        if not count:
            return {"total_meals": 0}
        week = nutrition_history(
            patient_id, (date.today() - timedelta(days=days - 1)).isoformat(), date.today().isoformat(),
        )
        return {
            "total_meals": count,
            "first": first.date().isoformat() if first else None,
            "last": last.date().isoformat() if last else None,
            "week": week,
        }
    except Exception as e:
        logger.warning("chat context: nutrition overview failed: %s", e)
        return {}
