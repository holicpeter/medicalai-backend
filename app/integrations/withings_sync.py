"""
Zápis Withings dát do kanonickej tabuľky health_records.

Bez tohto Withings existuje len ako endpoint, ktorý vracia dáta do prehliadača.
Chat, Trendy, Riziká ani modely ho nevidia — tie čítajú výhradne cez
app.analysis.sources, teda z health_records a apple_health_data.

Granularita je jeden riadok na metriku a deň. Zodpovedá to tomu, že
HealthRecord.record_date je Date, a bráni to tomu, aby tep meraný každých pár
minút zaplavil tabuľku stovkami riadkov denne.

Sync je idempotentný: existujúci riadok pre tú istú dvojicu (metrika, deň) sa
prepíše, nevytvorí sa duplikát. Môžeš ho teda spúšťať opakovane.
"""
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.database import get_session, HealthRecord, Patient
from app.integrations.withings_connector import get_withings_connector

logger = logging.getLogger(__name__)

SOURCE = "withings"
RECORD_TYPE = "wearable"

# Withings meastype -> názov metriky.
# POZOR: názvy musia presne sedieť s APPLE_TO_METRIC v app/analysis/sources.py,
# inak sa tá istá veličina objaví v trendoch ako dve samostatné krivky.
MEASURE_TO_METRIC = {
    "heart_rate": "heart_rate",
    "spo2": "oxygen_saturation",
    "weight": "weight",
    "height": "height",
    "body_temperature": "body_temperature",
    "skin_temperature": "skin_temperature",
    "vo2max": "vo2max",
    "pulse_wave_velocity": "pulse_wave_velocity",
}

# Jednotky, ktoré posielame do normalize(). Pri metrikách mimo CANONICAL sa
# hodnota nechá tak, ako je — normalize neháda.
METRIC_UNITS = {
    "heart_rate": "bpm",
    "oxygen_saturation": "%",
    "weight": "kg",
    "height": "m",          # Withings dáva metre, normalize prepočíta na cm
    "body_temperature": "degC",
    "skin_temperature": "degC",
    "vo2max": "ml/kg/min",
    "pulse_wave_velocity": "m/s",
    "sleep_duration": "h",
    "sleep_score": "score",
    "respiration_rate": "1/min",
    "hrv_rmssd": "ms",
    "hrv_sdnn": "ms",
}


def _default_patient_id(session) -> Optional[int]:
    patient = session.query(Patient).first()
    return patient.id if patient else None


def _day(iso: str):
    """ISO reťazec s offsetom -> dátum. Časy z konektora sú v UTC."""
    return datetime.fromisoformat(iso).date()


async def collect_daily_metrics(days: int = 30, hrv_nights: int = 14) -> Dict:
    """
    Stiahne z Withings všetko podstatné a zloží to na denné hodnoty.

    Vracia {(metrika, dátum): hodnota}. Kroky a vzdialenosť zámerne vynechávame
    — tie rieši aktivitná časť appky, nie interpretované metriky.
    """
    connector = get_withings_connector()
    daily: Dict[tuple, List[float]] = defaultdict(list)

    # --- bodové merania (tep, SpO2, teplota, váha, VO2max) ---
    for m in await connector.get_measures(days):
        metric = MEASURE_TO_METRIC.get(m["metric"])
        if not metric:
            continue
        daily[(metric, _day(m["measured_at"]))].append(m["value"])

    # --- spánok ---
    for night in await connector.get_sleep(days):
        day = _day(night["to"])  # noc pripisujeme ránu, keď sa skončila
        if night.get("total_sleep_seconds"):
            daily[("sleep_duration", day)].append(night["total_sleep_seconds"] / 3600)
        if night.get("sleep_score") is not None:
            daily[("sleep_score", day)].append(float(night["sleep_score"]))
        if night.get("respiration_rate") is not None:
            daily[("respiration_rate", day)].append(float(night["respiration_rate"]))
        # Nočný priemerný tep je najbližšie k pokojovému tepu, aké z hodiniek
        # dostaneme — spot merania cez deň sú ovplyvnené aktivitou.
        if night.get("hr_average") is not None:
            daily[("resting_heart_rate", day)].append(float(night["hr_average"]))

    # --- HRV ---
    try:
        for night in await connector.get_sleep_hrv(hrv_nights):
            day = _day(night["to"])
            if night["rmssd"].get("median") is not None:
                daily[("hrv_rmssd", day)].append(night["rmssd"]["median"])
            if night["sdnn"].get("median") is not None:
                daily[("hrv_sdnn", day)].append(night["sdnn"]["median"])
    except Exception as e:
        # HRV je pomalé a najkrehkejšie — nesmie zhodiť zvyšok syncu.
        logger.warning("[WITHINGS SYNC] HRV sa nepodarilo stiahnuť: %s", e)

    # Medián je odolnejší voči jednotlivým chybným meraniam než priemer.
    return {key: statistics.median(vals) for key, vals in daily.items() if vals}


async def sync_withings_to_db(days: int = 30, hrv_nights: int = 14) -> Dict:
    """Stiahne dáta a zapíše ich do health_records. Idempotentné."""
    values = await collect_daily_metrics(days, hrv_nights)
    if not values:
        return {"written": 0, "updated": 0, "metrics": []}

    session = get_session()
    written = updated = 0
    try:
        patient_id = _default_patient_id(session)

        existing = {
            (r.metric_type, r.record_date): r
            for r in session.query(HealthRecord)
            .filter(HealthRecord.source == SOURCE)
            .filter(HealthRecord.record_date >= (datetime.now().date()
                                                 - timedelta(days=days + 1)))
            .all()
        }

        for (metric, day), value in values.items():
            text_value = f"{round(float(value), 2)}"
            unit = METRIC_UNITS.get(metric)

            row = existing.get((metric, day))
            if row is not None:
                if row.value != text_value:
                    row.value = text_value
                    row.unit = unit
                    updated += 1
                continue

            session.add(HealthRecord(
                patient_id=patient_id,
                record_type=RECORD_TYPE,
                record_date=day,
                source=SOURCE,
                metric_type=metric,
                value=text_value,
                unit=unit,
            ))
            written += 1

        session.commit()
        logger.info(
            "[WITHINGS SYNC] %d nových, %d aktualizovaných záznamov",
            written, updated,
        )
    except Exception as e:
        session.rollback()
        logger.error("[WITHINGS SYNC] Zápis zlyhal: %s", e)
        raise
    finally:
        session.close()

    return {
        "written": written,
        "updated": updated,
        "metrics": sorted({m for m, _ in values.keys()}),
        "days": days,
    }
