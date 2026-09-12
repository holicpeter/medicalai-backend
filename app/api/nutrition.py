import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.database import get_session, Patient, NutritionEntry
from app.nutrition.analyzer import MealAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])
analyzer = MealAnalyzer()

_ALLOWED_MEDIA_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/heic', 'image/heif'}
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB — fotka z mobilu s rezervou

# Orientačné denné ciele, zatiaľ jednotné pre všetkých používateľov.
# Fáza 2/3: nahradiť hodnotami odvodenými z profilu pacienta (Patient).
_DEFAULT_DAILY_TARGETS = {
    'calories': 2000,
    'protein_g': 90,
    'carbs_g': 250,
    'fat_g': 70,
}


class FoodItemModel(BaseModel):
    name: str
    estimated_grams: float
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    confidence: float = 0.5


class NutritionEntryCreate(BaseModel):
    items: List[FoodItemModel]
    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    overall_confidence: Optional[float] = None
    recommendation: Optional[str] = None
    notes: Optional[str] = None


def _get_default_patient(session) -> Patient:
    patient = session.query(Patient).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


def _serialize_entry(entry: NutritionEntry) -> dict:
    return {
        "id": entry.id,
        "logged_at": entry.logged_at.isoformat() if entry.logged_at else None,
        "items": entry.items or [],
        "total_calories": entry.total_calories,
        "total_protein_g": entry.total_protein_g,
        "total_carbs_g": entry.total_carbs_g,
        "total_fat_g": entry.total_fat_g,
        "overall_confidence": entry.overall_confidence,
        "recommendation": entry.recommendation,
        "notes": entry.notes,
    }


def _day_bounds(day: date) -> tuple:
    start = datetime.combine(day, datetime.min.time())
    return start, start + timedelta(days=1)


@router.post("/analyze")
async def analyze_meal_photo(file: UploadFile = File(...)):
    """Odfotené jedlo -> Claude vision -> štruktúrovaný odhad nutričných hodnôt.

    Toto len analyzuje a vráti výsledok, neukladá nič do denníka — uloženie
    (prípadne po úprave porcie používateľom) ide cez POST /entries.
    """
    content_type = (file.content_type or '').split(';')[0].strip().lower()
    if content_type not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Nepodporovaný typ obrázka '{content_type or 'unknown'}'. "
                   f"Povolené: {sorted(_ALLOWED_MEDIA_TYPES)}",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Prázdny súbor.")
    if len(image_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fotka je príliš veľká (max 8 MB).")

    try:
        # Blocking Claude API call beží v samostatnom threade, rovnaký prístup
        # ako pri OCR (app/api/upload.py), nech neblokuje event loop.
        result = await asyncio.to_thread(
            analyzer.analyze_meal_photo, image_bytes, content_type,
        )
    except RuntimeError as e:
        logger.error('Meal analysis not configured: %s', e)
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        logger.warning('Meal analysis returned unparseable output: %s', e)
        raise HTTPException(
            status_code=502,
            detail="AI model nevrátil použiteľný výsledok. Skús to prosím znova, ideálne s jasnejšou fotkou.",
        )
    except Exception as e:
        logger.error('Meal analysis failed: %s', e)
        raise HTTPException(status_code=500, detail="Nastala neočakávaná chyba pri analýze fotky.")

    result['disclaimer'] = (
        'Toto je len orientačný odhad na základe fotky, nie presné laboratórne meranie. '
        'Skutočná gramáž a nutričné hodnoty sa môžu líšiť, najmä pri zmiešaných jedlách. '
        'Uprav porciu, ak si myslíš, že odhad nesedí.'
    )
    return result


@router.post("/entries")
async def save_nutrition_entry(data: NutritionEntryCreate):
    """Uloží (prípadne používateľom upravenú) analýzu jedla do denníka."""
    session = get_session()
    try:
        patient = _get_default_patient(session)

        entry = NutritionEntry(
            patient_id=patient.id,
            logged_at=datetime.now(),
            items=[item.model_dump() for item in data.items],
            total_calories=data.total_calories,
            total_protein_g=data.total_protein_g,
            total_carbs_g=data.total_carbs_g,
            total_fat_g=data.total_fat_g,
            overall_confidence=data.overall_confidence,
            recommendation=data.recommendation,
            notes=data.notes,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)

        return _serialize_entry(entry)
    finally:
        session.close()


@router.get("/entries")
async def list_nutrition_entries(target_date: Optional[date] = None):
    """Zoznam zaznamenaných jedál pre daný deň (default: dnes), najnovšie prvé."""
    day = target_date or date.today()
    start, end = _day_bounds(day)

    session = get_session()
    try:
        patient = _get_default_patient(session)
        entries = (
            session.query(NutritionEntry)
            .filter(
                NutritionEntry.patient_id == patient.id,
                NutritionEntry.logged_at >= start,
                NutritionEntry.logged_at < end,
            )
            .order_by(NutritionEntry.logged_at.desc())
            .all()
        )
        return [_serialize_entry(e) for e in entries]
    finally:
        session.close()


@router.delete("/entries/{entry_id}")
async def delete_nutrition_entry(entry_id: int):
    session = get_session()
    try:
        entry = session.query(NutritionEntry).filter_by(id=entry_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Záznam sa nenašiel.")
        session.delete(entry)
        session.commit()
        return {"success": True}
    finally:
        session.close()


@router.get("/summary")
async def get_daily_summary(target_date: Optional[date] = None):
    """Súčet kalórií/makier za daný deň + jednoduché pravidlové odporúčanie.

    Toto je zámerne "hlúpe" pravidlové odporúčanie, nie AI — plná personalizácia
    naviazaná na anamnézu a rodinné riziká je Fáza 3 (feature-analysis-nutrition-photo.md).
    """
    day = target_date or date.today()
    start, end = _day_bounds(day)

    session = get_session()
    try:
        patient = _get_default_patient(session)
        entries = (
            session.query(NutritionEntry)
            .filter(
                NutritionEntry.patient_id == patient.id,
                NutritionEntry.logged_at >= start,
                NutritionEntry.logged_at < end,
            )
            .all()
        )
    finally:
        session.close()

    totals = {
        'calories': round(sum(e.total_calories or 0 for e in entries), 1),
        'protein_g': round(sum(e.total_protein_g or 0 for e in entries), 1),
        'carbs_g': round(sum(e.total_carbs_g or 0 for e in entries), 1),
        'fat_g': round(sum(e.total_fat_g or 0 for e in entries), 1),
    }

    return {
        'date': day.isoformat(),
        'totals': totals,
        'targets': _DEFAULT_DAILY_TARGETS,
        'meals_logged': len(entries),
        'recommendation': _build_recommendation(totals, len(entries)),
    }


def _build_recommendation(totals: dict, meals_logged: int) -> str:
    if meals_logged == 0:
        return 'Zatiaľ dnes nemáš zaznamenané žiadne jedlo. Odfoť svoje prvé jedlo a začni sledovať príjem.'

    targets = _DEFAULT_DAILY_TARGETS
    notes = []

    protein_ratio = totals['protein_g'] / targets['protein_g'] if targets['protein_g'] else 0
    calories_ratio = totals['calories'] / targets['calories'] if targets['calories'] else 0

    if protein_ratio < 0.6:
        notes.append('príjem bielkovín je zatiaľ nízky vzhľadom na denný cieľ – zváž pridať zdroj bielkovín k ďalšiemu jedlu')
    elif protein_ratio > 1.3:
        notes.append('príjem bielkovín dnes prekročil bežný denný cieľ')

    if calories_ratio > 1.1:
        notes.append('celkový kalorický príjem už prekročil orientačný denný cieľ')
    elif calories_ratio < 0.4 and meals_logged >= 2:
        notes.append('celkový kalorický príjem je zatiaľ dosť nízky vzhľadom na počet zaznamenaných jedál – over, či niečo nechýba v denníku')

    if not notes:
        return 'Dnešný príjem vyzerá zatiaľ vyvážený vzhľadom na orientačné denné ciele.'

    return f"Poznámka k dnešnému dňu: {'; '.join(notes)}."
