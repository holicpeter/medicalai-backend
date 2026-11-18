"""Tréningový denník: cviky so sériami, história cvikov a progres.

Záznamy zadáva používateľ sám, takže ukladanie, história aj progres sú bez AI
a nemíňajú kredity. AI sa volá len pri rozpoznaní stroja z fotky
(POST /identify), ktoré nič neukladá — rovnako ako /api/nutrition/analyze.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth.dependencies import get_current_patient_id, get_current_user
from app.auth.quota import ai_call
from app.database import WorkoutEntry, get_session
from app.i18n import tr
from app.workouts.identifier import MachineIdentifier
from app.workouts.progress import daily_points, exercise_key, progress_summary, set_metrics

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workouts", tags=["workouts"])
identifier = MachineIdentifier()

_ALLOWED_MEDIA_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/heic', 'image/heif'}
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_MAX_RANGE_DAYS = 400


class WorkoutSet(BaseModel):
    reps: int = Field(0, ge=0, le=1000)
    weight_kg: float = Field(0, ge=0, le=1000)


class WorkoutEntryIn(BaseModel):
    exercise: str = Field(..., min_length=1, max_length=200)
    category: str = Field('strength', pattern='^(strength|cardio|other)$')
    sets: List[WorkoutSet] = Field(default_factory=list, max_length=50)
    duration_min: Optional[float] = Field(None, ge=0, le=1440)
    distance_km: Optional[float] = Field(None, ge=0, le=1000)
    notes: Optional[str] = Field(None, max_length=2000)
    source: str = Field('manual', pattern='^(manual|photo)$')
    # Kedy sa cvičilo. Bez neho teraz; naivný lokálny čas ako pri jedlách.
    logged_at: Optional[datetime] = None

    @field_validator('exercise')
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = ' '.join(value.split())
        if not value:
            raise ValueError(tr("Zadajte názov cviku.", "Enter the exercise name."))
        return value

    @field_validator('logged_at')
    @classmethod
    def _not_in_future_or_too_old(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        now = datetime.now()
        if value > now + timedelta(days=1):
            raise ValueError(tr("Tréning nemôže byť v budúcnosti.", "A workout cannot be in the future."))
        if value < now - timedelta(days=366):
            raise ValueError(tr("Tréning môžete pridať najviac rok dozadu.",
                                "Workouts can be added at most one year back."))
        return value

    @model_validator(mode='after')
    def _something_was_done(self):
        has_sets = any(s.reps > 0 for s in self.sets)
        if not has_sets and not self.duration_min and not self.distance_km:
            raise ValueError(tr("Zadajte aspoň jednu sériu s opakovaniami, trvanie alebo vzdialenosť.",
                                "Enter at least one set with reps, a duration or a distance."))
        return self


def _serialize(entry: WorkoutEntry) -> dict:
    return {
        "id": entry.id,
        "logged_at": entry.logged_at.isoformat() if entry.logged_at else None,
        "exercise": entry.exercise,
        "category": entry.category or 'strength',
        "sets": entry.sets or [],
        "duration_min": entry.duration_min,
        "distance_km": entry.distance_km,
        "notes": entry.notes,
        "source": entry.source or 'manual',
        **set_metrics(entry.sets),
    }


def _apply(entry: WorkoutEntry, data: WorkoutEntryIn) -> None:
    entry.exercise = data.exercise
    entry.exercise_key = exercise_key(data.exercise)
    entry.category = data.category
    # Prázdne série (0 opakovaní) z formulára nemajú zmysel ukladať.
    entry.sets = [s.model_dump() for s in data.sets if s.reps > 0]
    entry.duration_min = data.duration_min
    entry.distance_km = data.distance_km
    entry.notes = (data.notes or '').strip() or None
    entry.source = data.source


def _day_bounds(day: date) -> tuple:
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1)


def _own_entry(session, entry_id: int, patient_id: int) -> WorkoutEntry:
    # Podľa patient_id, nielen id — inak by sa dal cudzí záznam čítať/zmazať
    # uhádnutím id (IDOR).
    entry = session.query(WorkoutEntry).filter_by(id=entry_id, patient_id=patient_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail=tr("Záznam sa nenašiel.", "Entry not found."))
    return entry


@router.post("/entries")
async def create_workout_entry(
    data: WorkoutEntryIn,
    patient_id: int = Depends(get_current_patient_id),
):
    session = get_session()
    try:
        entry = WorkoutEntry(patient_id=patient_id, logged_at=data.logged_at or datetime.now())
        _apply(entry, data)
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _serialize(entry)
    finally:
        session.close()


@router.put("/entries/{entry_id}")
async def update_workout_entry(
    entry_id: int,
    data: WorkoutEntryIn,
    patient_id: int = Depends(get_current_patient_id),
):
    session = get_session()
    try:
        entry = _own_entry(session, entry_id, patient_id)
        _apply(entry, data)
        if data.logged_at:
            entry.logged_at = data.logged_at
        session.commit()
        session.refresh(entry)
        return _serialize(entry)
    finally:
        session.close()


@router.get("/entries")
async def list_workout_entries(
    target_date: Optional[date] = None,
    patient_id: int = Depends(get_current_patient_id),
):
    """Cviky z daného dňa (default dnes) v poradí, v akom sa cvičili."""
    start, end = _day_bounds(target_date or date.today())
    session = get_session()
    try:
        entries = (
            session.query(WorkoutEntry)
            .filter(
                WorkoutEntry.patient_id == patient_id,
                WorkoutEntry.logged_at >= start,
                WorkoutEntry.logged_at < end,
            )
            .order_by(WorkoutEntry.logged_at, WorkoutEntry.id)
            .all()
        )
        return [_serialize(e) for e in entries]
    finally:
        session.close()


@router.delete("/entries/{entry_id}")
async def delete_workout_entry(
    entry_id: int,
    patient_id: int = Depends(get_current_patient_id),
):
    session = get_session()
    try:
        session.delete(_own_entry(session, entry_id, patient_id))
        session.commit()
        return {"success": True}
    finally:
        session.close()


@router.get("/days")
async def workout_days(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    patient_id: int = Depends(get_current_patient_id),
):
    """Dni, v ktoré sa cvičilo (pre kalendár), s počtom cvikov a objemom."""
    end = end_date or date.today()
    start = start_date or end - timedelta(days=41)
    if start > end or (end - start).days > _MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=tr("Neplatné obdobie.", "Invalid date range."))

    session = get_session()
    try:
        entries = (
            session.query(WorkoutEntry)
            .filter(
                WorkoutEntry.patient_id == patient_id,
                WorkoutEntry.logged_at >= datetime.combine(start, datetime.min.time()),
                WorkoutEntry.logged_at < datetime.combine(end + timedelta(days=1), datetime.min.time()),
            )
            .all()
        )
    finally:
        session.close()

    days: dict = {}
    for e in entries:
        d = days.setdefault(e.logged_at.date().isoformat(), {"exercises": 0, "volume_kg": 0.0, "duration_min": 0.0})
        d["exercises"] += 1
        d["volume_kg"] = round(d["volume_kg"] + set_metrics(e.sets)["volume_kg"], 1)
        d["duration_min"] = round(d["duration_min"] + (e.duration_min or 0), 1)
    return [{"date": k, **v} for k, v in sorted(days.items())]


def _history(session, patient_id: int) -> List[dict]:
    entries = (
        session.query(WorkoutEntry)
        .filter(WorkoutEntry.patient_id == patient_id)
        .order_by(WorkoutEntry.logged_at, WorkoutEntry.id)
        .all()
    )
    by_key: dict = {}
    for e in entries:
        item = by_key.setdefault(e.exercise_key, {"sessions": set(), "best_weight_kg": None})
        metrics = set_metrics(e.sets)
        best = metrics["best_weight_kg"]
        if best is not None and (item["best_weight_kg"] is None or best > item["best_weight_kg"]):
            item["best_weight_kg"] = best
        item["sessions"].add(e.logged_at.date())
        # Entries are in time order, so the last one wins: newest name and sets.
        item.update({
            "exercise": e.exercise,
            "exercise_key": e.exercise_key,
            "category": e.category or 'strength',
            "last_date": e.logged_at.date().isoformat(),
            "last_sets": e.sets or [],
            "last_duration_min": e.duration_min,
            "last_distance_km": e.distance_km,
        })
    out = [{**v, "sessions": len(v["sessions"])} for v in by_key.values()]
    out.sort(key=lambda x: x["last_date"], reverse=True)
    return out


@router.get("/exercises")
async def list_exercises(patient_id: int = Depends(get_current_patient_id)):
    """Všetky cviky, ktoré už používateľ zapísal, s posledným výkonom.

    Slúži na našepkávanie pri zadávaní nového cviku: vybraný cvik sa
    predvyplní sériami z minula, takže stačí upraviť váhu alebo opakovania.
    """
    session = get_session()
    try:
        return _history(session, patient_id)
    finally:
        session.close()


@router.get("/progress")
async def exercise_progress(
    exercise: str,
    patient_id: int = Depends(get_current_patient_id),
):
    """Vývoj jedného cviku v čase: bod za každý deň + porovnanie prvý/posledný."""
    key = exercise_key(exercise)
    session = get_session()
    try:
        entries = (
            session.query(WorkoutEntry)
            .filter(WorkoutEntry.patient_id == patient_id, WorkoutEntry.exercise_key == key)
            .order_by(WorkoutEntry.logged_at, WorkoutEntry.id)
            .all()
        )
        if not entries:
            raise HTTPException(status_code=404, detail=tr("Tento cvik ešte nemáte zapísaný.",
                                                           "You have not logged this exercise yet."))
        points = daily_points(entries)
        return {
            "exercise": entries[-1].exercise,
            "exercise_key": key,
            "category": entries[-1].category or 'strength',
            "points": points,
            "summary": progress_summary(points),
        }
    finally:
        session.close()


@router.post("/identify")
async def identify_machine(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    patient_id: int = Depends(get_current_patient_id),
):
    """Fotka stroja -> AI povie, aký cvik sa na ňom robí. Nič neukladá.

    Do promptu ide zoznam cvikov, ktoré už používateľ zapisuje, aby AI vrátila
    rovnaký názov a nový záznam sa pripojil k existujúcej histórii.
    """
    content_type = (file.content_type or '').split(';')[0].strip().lower()
    if content_type not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=tr(
            f"Nepodporovaný typ obrázka '{content_type or 'unknown'}'.",
            f"Unsupported image type '{content_type or 'unknown'}'.",
        ))
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail=tr("Prázdny súbor.", "Empty file."))
    if len(image_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=tr("Fotka je príliš veľká (max 8 MB).",
                                                       "The photo is too large (max 8 MB)."))

    session = get_session()
    try:
        history = _history(session, patient_id)
    finally:
        session.close()

    with ai_call(user, "workouts"):
        try:
            result = await asyncio.to_thread(
                identifier.identify, image_bytes, content_type, [h["exercise"] for h in history],
            )
        except RuntimeError as e:
            logger.error('Machine identification not configured: %s', e)
            raise HTTPException(status_code=500, detail=str(e))
        except ValueError as e:
            logger.warning('Machine identification returned unparseable output: %s', e)
            raise HTTPException(status_code=502, detail=tr(
                "AI model nevrátil použiteľný výsledok. Skúste to znova s fotkou celého stroja.",
                "The AI model did not return a usable result. Please try again with a photo of the whole machine.",
            ))
        except Exception as e:
            logger.error('Machine identification failed: %s', e)
            raise HTTPException(status_code=500, detail=tr("Nastala neočakávaná chyba pri analýze fotky.",
                                                           "An unexpected error occurred while analysing the photo."))

    # Ak AI vrátila cvik, ktorý už používateľ má, priložíme jeho posledný výkon.
    known = {h["exercise_key"]: h for h in history}
    result["history"] = known.get(exercise_key(result["exercise"])) if result["exercise"] else None
    result["disclaimer"] = tr(
        "Rozpoznanie z fotky je orientačné. Pri neznámom stroji si nechajte techniku ukázať trénerom.",
        "Recognition from a photo is approximate. For an unfamiliar machine, ask a trainer to show you the technique.",
    )
    return result
