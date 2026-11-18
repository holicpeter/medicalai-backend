"""Výpočty pre tréningový denník: normalizácia názvu cviku a metriky progresu.

Všetko tu je deterministické (žiadne AI) — progres je aritmetika nad sériami,
ktoré zadal používateľ, takže sa dá overiť a nemíňa kredity.
"""
import re
import unicodedata
from datetime import date
from typing import Dict, Iterable, List, Optional

# Epleyho odhad 1RM je spoľahlivý len pri nízkom počte opakovaní; pri
# vytrvalostných sériách (15+) by sľuboval silu, ktorú človek nemá.
_MAX_REPS_FOR_1RM = 12


def exercise_key(name: str) -> str:
    """„Bench Press", „bench  press" a „Benčpres" nie sú to isté, ale prvé dva áno."""
    decomposed = unicodedata.normalize('NFKD', name or '')
    ascii_only = ''.join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', ascii_only).strip().lower()[:200]


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def set_metrics(sets: Optional[Iterable[dict]]) -> Dict[str, Optional[float]]:
    """Najťažšia váha, odhad 1RM, objem (opakovania × váha) a počet opakovaní."""
    best_weight = None
    best_1rm = None
    volume = 0.0
    total_reps = 0
    for s in sets or []:
        reps = int(_num(s.get('reps')))
        weight = _num(s.get('weight_kg'))
        total_reps += reps
        volume += reps * weight
        if weight > 0 and reps > 0:
            best_weight = weight if best_weight is None else max(best_weight, weight)
            if reps <= _MAX_REPS_FOR_1RM:
                one_rm = weight * (1 + reps / 30) if reps > 1 else weight
                best_1rm = one_rm if best_1rm is None else max(best_1rm, one_rm)
    return {
        'best_weight_kg': round(best_weight, 1) if best_weight is not None else None,
        'est_1rm_kg': round(best_1rm, 1) if best_1rm is not None else None,
        'volume_kg': round(volume, 1),
        'total_reps': total_reps,
    }


def daily_points(entries: List) -> List[Dict]:
    """Jeden bod na deň: viac záznamov toho istého cviku v jeden deň sa spojí."""
    by_day: Dict[date, Dict] = {}
    for e in entries:
        day = e.logged_at.date()
        point = by_day.setdefault(day, {'sets': [], 'duration_min': 0.0, 'distance_km': 0.0,
                                        'has_duration': False, 'has_distance': False})
        point['sets'].extend(e.sets or [])
        if e.duration_min is not None:
            point['duration_min'] += e.duration_min
            point['has_duration'] = True
        if e.distance_km is not None:
            point['distance_km'] += e.distance_km
            point['has_distance'] = True

    out = []
    for day in sorted(by_day):
        p = by_day[day]
        metrics = set_metrics(p['sets'])
        duration = round(p['duration_min'], 1) if p['has_duration'] else None
        distance = round(p['distance_km'], 2) if p['has_distance'] else None
        pace = round(duration / distance, 2) if duration and distance else None
        out.append({
            'date': day.isoformat(),
            **metrics,
            'sets': len(p['sets']),
            'duration_min': duration,
            'distance_km': distance,
            'pace_min_per_km': pace,
        })
    return out


# Ktorá metrika najlepšie vystihuje zlepšenie, v poradí preferencie, a či je
# lepšia vyššia hodnota (tempo je lepšie nižšie).
_PROGRESS_METRICS = (
    ('est_1rm_kg', True),
    ('best_weight_kg', True),
    ('distance_km', True),
    ('pace_min_per_km', False),
    ('duration_min', True),
    ('total_reps', True),
)


def progress_summary(points: List[Dict]) -> Dict:
    """Porovnanie prvého a posledného tréningu na metrike, ktorú cvik naozaj má."""
    summary: Dict = {
        'sessions': len(points),
        'first_date': points[0]['date'] if points else None,
        'last_date': points[-1]['date'] if points else None,
        'metric': None,
        'first': None,
        'last': None,
        'best': None,
        'best_date': None,
        'change_pct': None,
        'improved': None,
    }
    for metric, higher_is_better in _PROGRESS_METRICS:
        values = [(p['date'], p[metric]) for p in points if p.get(metric)]
        if not values:
            continue
        first, last = values[0][1], values[-1][1]
        pick = max if higher_is_better else min
        best_date, best = pick(values, key=lambda v: v[1])
        change = round((last - first) / first * 100, 1) if first and len(values) > 1 else None
        summary.update({
            'metric': metric,
            'first': first,
            'last': last,
            'best': best,
            'best_date': best_date,
            'change_pct': change,
            'improved': None if change is None else (change > 0 if higher_is_better else change < 0),
        })
        break
    return summary
