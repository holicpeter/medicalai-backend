import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import get_current_patient_id, get_current_user
from app.auth.quota import ai_call
from app.database import get_session, NutritionEntry, NutritionTextCache
from app.nutrition.analyzer import MealAnalyzer
from app.i18n import current_lang, tr

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
    name: str = Field(..., min_length=1, max_length=200)
    estimated_grams: float = Field(0, ge=0)
    calories: float = Field(..., ge=0)
    protein_g: float = Field(0, ge=0)
    carbs_g: float = Field(0, ge=0)
    fat_g: float = Field(0, ge=0)
    confidence: float = 0.5


class MealTextRequest(BaseModel):
    description: str = Field(..., min_length=3, max_length=2000)


class NutritionEntryCreate(BaseModel):
    items: List[FoodItemModel]
    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    overall_confidence: Optional[float] = None
    recommendation: Optional[str] = None
    notes: Optional[str] = None
    # When the meal was eaten. Omitted: now. Set when the user adds a meal to
    # an earlier day. Naive local time, like the stored logged_at values.
    logged_at: Optional[datetime] = None

    @field_validator("logged_at")
    @classmethod
    def _not_in_future_or_too_old(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        now = datetime.now()
        # A day of slack: the browser's clock and timezone are not the server's.
        if value > now + timedelta(days=1):
            raise ValueError(tr("Jedlo nemôže byť v budúcnosti.", "A meal cannot be in the future."))
        if value < now - timedelta(days=366):
            raise ValueError(tr("Jedlo môžete pridať najviac rok dozadu.",
                                "Meals can be added at most one year back."))
        return value


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


def _normalize_description(text: str) -> str:
    """Normalizuje popis jedla pre cache lookup.

    Zámerne jednoduché a konzervatívne — case-insensitive, orezané a zjednotené
    biele znaky. Cieľom je chytiť presne ten istý (alebo takmer identicky
    napísaný) popis znova, nie robiť fuzzy matching naprieč rôznymi jedlami.
    Pri zdravotnej appke je nesprávne priradený odhad horší než jedno AI
    volanie navyše, tak radšej cache miss než falošný hit.
    """
    normalized = text.strip().lower()
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized[:2000]


@router.post("/analyze")
async def analyze_meal_photo(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Odfotené jedlo -> Claude vision -> štruktúrovaný odhad nutričných hodnôt.

    Toto len analyzuje a vráti výsledok, neukladá nič do denníka — uloženie
    (prípadne po úprave porcie používateľom) ide cez POST /entries.
    """
    content_type = (file.content_type or '').split(';')[0].strip().lower()
    if content_type not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=tr(f"Nepodporovaný typ obrázka '{content_type or 'unknown'}'. "
                      f"Povolené: {sorted(_ALLOWED_MEDIA_TYPES)}",
                      f"Unsupported image type '{content_type or 'unknown'}'. "
                      f"Allowed: {sorted(_ALLOWED_MEDIA_TYPES)}"),
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail=tr("Prázdny súbor.", "Empty file."))
    if len(image_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=tr("Fotka je príliš veľká (max 8 MB).", "The photo is too large (max 8 MB)."))

    with ai_call(user, "nutrition"):
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
                detail=tr("AI model nevrátil použiteľný výsledok. Skús to prosím znova, ideálne s jasnejšou fotkou.",
                      "The AI model did not return a usable result. Please try again, ideally with a clearer photo."),
            )
        except Exception as e:
            logger.error('Meal analysis failed: %s', e)
            raise HTTPException(status_code=500, detail=tr("Nastala neočakávaná chyba pri analýze fotky.", "An unexpected error occurred while analysing the photo."))

    result['disclaimer'] = tr(
        'Toto je len orientačný odhad na základe fotky, nie presné laboratórne meranie. '
        'Skutočná gramáž a nutričné hodnoty sa môžu líšiť, najmä pri zmiešaných jedlách. '
        'Uprav porciu, ak si myslíš, že odhad nesedí.',
        'This is only a rough estimate based on the photo, not a precise laboratory measurement. '
        'Actual portion sizes and nutrition values may differ, especially for mixed dishes. '
        "Adjust the portion if you think the estimate is off.",
    )
    result['from_cache'] = False  # fotky sa necachujú (pozri NutritionTextCache docstring)
    return result


def _text_disclaimer() -> str:
    return tr(
        'Toto je len orientačný odhad na základe textového popisu, nie presné laboratórne meranie. '
        'Skutočná gramáž a nutričné hodnoty sa môžu líšiť najmä pri chýbajúcich detailoch (príprava, omáčky). '
        'Uprav hodnoty, ak si myslíš, že odhad nesedí.',
        'This is only a rough estimate based on the description, not a precise laboratory measurement. '
        'Actual portion sizes and nutrition values may differ, especially when details are missing '
        "(preparation, sauces). Adjust the values if you think the estimate is off.",
    )


@router.post("/analyze-text")
async def analyze_meal_text(
    data: MealTextRequest,
    user=Depends(get_current_user),
):
    """Textový popis jedla -> Claude -> štruktúrovaný odhad nutričných hodnôt.

    Rovnaký kontrakt ako POST /analyze (fotka): len analyzuje a vráti výsledok,
    neukladá nič do denníka — uloženie ide rovnako cez POST /entries, ktoré je
    zdroju analýzy (fotka vs. text) ľahostajné.

    Pred volaním AI sa skúsi presný (normalizovaný) match v NutritionTextCache —
    ak niekto napíše rovnaký popis znova (rutinné raňajky/obed), ušetrí to celé
    volanie Claude API. Pozri ai-model-selection-strategy.md, krok 2.
    """
    normalized = _normalize_description(data.description)
    if current_lang() == "en":
        # Food names in the cached result are in the language they were
        # analysed in. Slovak keys stay as they were, so the existing cache keeps working.
        normalized = f"en:{normalized}"[:2000]

    session = get_session()
    try:
        cached = (
            session.query(NutritionTextCache)
            .filter_by(description_normalized=normalized)
            .first()
        )
        if cached:
            cached.hit_count = (cached.hit_count or 0) + 1
            cached.last_used_at = datetime.now()
            session.commit()
            result = dict(cached.analysis_json)
            result['disclaimer'] = _text_disclaimer()
            result['from_cache'] = True
            logger.info('Nutrition text cache hit (hit_count=%s)', cached.hit_count)
            return result
    finally:
        session.close()

    with ai_call(user, "nutrition"):  # cache hits above are free
        try:
            result = await asyncio.to_thread(
                analyzer.analyze_meal_text, data.description,
            )
        except RuntimeError as e:
            logger.error('Meal text analysis not configured: %s', e)
            raise HTTPException(status_code=500, detail=str(e))
        except ValueError as e:
            logger.warning('Meal text analysis returned unparseable output: %s', e)
            raise HTTPException(
                status_code=502,
                detail=tr("AI model nevrátil použiteľný výsledok. Skús to prosím znova, ideálne s podrobnejším popisom.",
                      "The AI model did not return a usable result. Please try again, ideally with a more detailed description."),
            )
        except Exception as e:
            logger.error('Meal text analysis failed: %s', e)
            raise HTTPException(status_code=500, detail=tr("Nastala neočakávaná chyba pri analýze popisu jedla.", "An unexpected error occurred while analysing the meal description."))

    # Cache zápis je best-effort — ak zlyhá (napr. súbežný rovnaký request
    # vyhral unique constraint pretek), analýza sa aj tak vráti používateľovi.
    cache_session = get_session()
    try:
        cache_session.add(NutritionTextCache(
            description_normalized=normalized,
            description_original=data.description,
            analysis_json=result,
            hit_count=1,
        ))
        cache_session.commit()
    except IntegrityError:
        cache_session.rollback()
    except Exception as e:
        cache_session.rollback()
        logger.warning('Failed to write nutrition text cache: %s', e)
    finally:
        cache_session.close()

    result['disclaimer'] = _text_disclaimer()
    result['from_cache'] = False
    return result


@router.post("/entries")
async def save_nutrition_entry(
    data: NutritionEntryCreate,
    patient_id: int = Depends(get_current_patient_id),
):
    """Uloží jedlo do denníka: AI analýzu (prípadne upravenú) alebo ručne zadané hodnoty.

    Ručný záznam ide rovnakou cestou, len bez AI, takže nemíňa kredity.
    logged_at umožňuje doplniť jedlo aj do predchádzajúcich dní.
    """
    session = get_session()
    try:
        entry = NutritionEntry(
            patient_id=patient_id,
            logged_at=data.logged_at or datetime.now(),
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
async def list_nutrition_entries(
    target_date: Optional[date] = None,
    patient_id: int = Depends(get_current_patient_id),
):
    """Zoznam zaznamenaných jedál pre daný deň (default: dnes), najnovšie prvé."""
    day = target_date or date.today()
    start, end = _day_bounds(day)

    session = get_session()
    try:
        entries = (
            session.query(NutritionEntry)
            .filter(
                NutritionEntry.patient_id == patient_id,
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
async def delete_nutrition_entry(
    entry_id: int,
    patient_id: int = Depends(get_current_patient_id),
):
    session = get_session()
    try:
        # Scoped by patient_id, not just id: an id-only lookup would let any
        # authenticated user delete another patient's entry by guessing/
        # incrementing the id (IDOR).
        entry = (
            session.query(NutritionEntry)
            .filter_by(id=entry_id, patient_id=patient_id)
            .first()
        )
        if not entry:
            raise HTTPException(status_code=404, detail=tr("Záznam sa nenašiel.", "Entry not found."))
        session.delete(entry)
        session.commit()
        return {"success": True}
    finally:
        session.close()


@router.get("/summary")
async def get_daily_summary(
    target_date: Optional[date] = None,
    patient_id: int = Depends(get_current_patient_id),
):
    """Súčet kalórií/makier za daný deň + jednoduché pravidlové odporúčanie.

    Toto je zámerne "hlúpe" pravidlové odporúčanie, nie AI — plná personalizácia
    naviazaná na anamnézu a rodinné riziká je Fáza 3 (feature-analysis-nutrition-photo.md).
    """
    day = target_date or date.today()
    start, end = _day_bounds(day)

    session = get_session()
    try:
        entries = (
            session.query(NutritionEntry)
            .filter(
                NutritionEntry.patient_id == patient_id,
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
        return tr('Zatiaľ dnes nemáš zaznamenané žiadne jedlo. Odfoť svoje prvé jedlo a začni sledovať príjem.',
                  "You haven't logged any meal today yet. Take a photo of your first meal to start tracking.")

    targets = _DEFAULT_DAILY_TARGETS
    notes = []

    protein_ratio = totals['protein_g'] / targets['protein_g'] if targets['protein_g'] else 0
    calories_ratio = totals['calories'] / targets['calories'] if targets['calories'] else 0

    if protein_ratio < 0.6:
        notes.append(tr('príjem bielkovín je zatiaľ nízky vzhľadom na denný cieľ – zváž pridať zdroj bielkovín k ďalšiemu jedlu',
                        'protein intake is low so far compared to the daily goal – consider adding a protein source to your next meal'))
    elif protein_ratio > 1.3:
        notes.append(tr('príjem bielkovín dnes prekročil bežný denný cieľ', 'protein intake today is above the usual daily goal'))

    if calories_ratio > 1.1:
        notes.append(tr('celkový kalorický príjem už prekročil orientačný denný cieľ', 'total calorie intake is already above the approximate daily goal'))
    elif calories_ratio < 0.4 and meals_logged >= 2:
        notes.append(tr('celkový kalorický príjem je zatiaľ dosť nízky vzhľadom na počet zaznamenaných jedál – over, či niečo nechýba v denníku',
                        'total calorie intake is quite low for the number of logged meals – check whether something is missing from the diary'))

    if not notes:
        return tr('Dnešný príjem vyzerá zatiaľ vyvážený vzhľadom na orientačné denné ciele.',
                  "Today's intake looks balanced so far compared to the approximate daily goals.")

    return tr("Poznámka k dnešnému dňu: ", "Note for today: ") + f"{'; '.join(notes)}."
