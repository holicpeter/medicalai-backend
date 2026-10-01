import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.auth.demo import is_demo_email
from app.auth.dependencies import get_current_user, require_admin
from app.auth.security import decode_access_token
from app.config import settings
from app.database import User

logger = logging.getLogger(__name__)

# Withings is per user: everyone connects their own Withings account and the
# tokens are stored per user (app/integrations/connections.py).
#
# Garmin and Google Calendar are still process-global singletons holding one
# session for the whole app, so they stay restricted to ADMIN_EMAILS via
# require_admin until they get the same per-user treatment.

try:
    from app.integrations.garmin_connector import get_garmin_connector
    GARMIN_AVAILABLE = True
except Exception as e:
    print(f"[INTEGRATIONS] Garmin connector not available: {e}")
    get_garmin_connector = None  # type: ignore
    GARMIN_AVAILABLE = False

try:
    from app.integrations.calendar_connector import get_calendar_connector
    CALENDAR_AVAILABLE = True
except Exception as e:
    print(f"[INTEGRATIONS] Calendar connector not available: {e}")
    get_calendar_connector = None  # type: ignore
    CALENDAR_AVAILABLE = False

try:
    from app.integrations.withings_connector import get_withings_connector
    WITHINGS_AVAILABLE = True
except Exception as e:
    print(f"[INTEGRATIONS] Withings connector not available: {e}")
    get_withings_connector = None  # type: ignore
    WITHINGS_AVAILABLE = False

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class GarminAuthRequest(BaseModel):
    email: str
    password: str


class SyncRequest(BaseModel):
    days: int = 30


class CorrelationAnalysisRequest(BaseModel):
    days: int = 30


@router.post("/garmin/auth")
async def authenticate_garmin(request: GarminAuthRequest, _admin=Depends(require_admin)):
    """
    Autentifikácia do Garmin Connect
    """
    try:
        if not GARMIN_AVAILABLE or get_garmin_connector is None:
            raise HTTPException(status_code=503, detail="Garmin integrácia nie je dostupná (chýba balík alebo závislosti).")
        connector = get_garmin_connector()
        success = await connector.authenticate(request.email, request.password)
        
        if success:
            return {
                "success": True,
                "message": "Successfully authenticated to Garmin Connect"
            }
        else:
            raise HTTPException(status_code=401, detail="Authentication failed")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/garmin/daily")
async def get_garmin_daily_data(date: Optional[str] = None, _admin=Depends(require_admin)):
    """
    Získať denné dáta z Garmin hodinek
    """
    try:
        if not GARMIN_AVAILABLE or get_garmin_connector is None:
            raise HTTPException(status_code=503, detail="Garmin integrácia nie je dostupná (chýba balík alebo závislosti).")
        connector = get_garmin_connector()
        
        if not connector.is_authenticated:
            raise HTTPException(
                status_code=401, 
                detail="Not authenticated. Please authenticate first."
            )
        
        data = await connector.get_daily_summary(date)
        return data
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/garmin/sync")
async def sync_garmin_data(request: SyncRequest, background_tasks: BackgroundTasks, _admin=Depends(require_admin)):
    """
    Synchronizovať historické dáta z Garmin (na pozadí)
    """
    try:
        if not GARMIN_AVAILABLE or get_garmin_connector is None:
            raise HTTPException(status_code=503, detail="Garmin integrácia nie je dostupná (chýba balík alebo závislosti).")
        connector = get_garmin_connector()
        
        if not connector.is_authenticated:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated. Please authenticate first."
            )
        
        # Spustiť sync na pozadí
        background_tasks.add_task(sync_garmin_background, request.days)
        
        return {
            "success": True,
            "message": f"Sync started for last {request.days} days",
            "status": "processing"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================================
# WITHINGS — ScanWatch 2 + Body Scan
# =====================================================================

def _withings_for(user: User):
    """The current user's Withings connector, or 401 when they have not connected Withings."""
    if not WITHINGS_AVAILABLE or get_withings_connector is None:
        raise HTTPException(
            status_code=503,
            detail="Withings integrácia nie je dostupná (chýba balík alebo závislosti).",
        )
    connector = get_withings_connector(user.id)
    if not connector.is_authenticated:
        raise HTTPException(status_code=401, detail="Withings nie je pripojený.")
    return connector


async def _withings_call(coro):
    from app.integrations.withings_connector import WithingsNotConnected
    try:
        return await coro
    except WithingsNotConnected as e:
        raise HTTPException(status_code=401, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def withings_authorize_url(user: User) -> str:
    """Withings' consent page for this user. Shared with app/api/connections.py."""
    if not WITHINGS_AVAILABLE or get_withings_connector is None:
        raise HTTPException(status_code=503, detail="Withings integrácia nie je dostupná.")
    if is_demo_email(user.email):
        raise HTTPException(status_code=403, detail="Ukážkový účet nemôže pripájať zariadenia.")
    connector = get_withings_connector(user.id)
    if not connector.is_configured:
        raise HTTPException(status_code=503, detail="Withings zatiaľ nie je na serveri nastavený.")
    from app.integrations.connections import make_oauth_state
    return connector.get_authorize_url(make_oauth_state(user.id, "withings"))


@router.get("/withings/auth")
async def authenticate_withings(user: User = Depends(get_current_user)):
    """
    Vráti URL, na ktorú treba používateľa presmerovať (OAuth2 consent screen)
    """
    connected = WITHINGS_AVAILABLE and get_withings_connector(user.id).is_authenticated
    return {"authorize_url": withings_authorize_url(user), "authenticated": connected}


def _back_to_app(**params) -> RedirectResponse:
    return RedirectResponse(
        f"{settings.APP_BASE_URL.rstrip('/')}/connections?{urlencode(params)}",
        status_code=303,
    )


@router.api_route("/withings/callback", methods=["GET", "HEAD", "POST"])
async def withings_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: Optional[str] = None,
    state: str = "",
    error: Optional[str] = None,
):
    """
    Sem Withings vráti prehliadač po tom, čo používateľ povolil prístup.

    Code expiruje za 30 sekúnd, preto sa vymieňa hneď. Prijíma GET, HEAD aj
    POST a bez `code` vracia 200: Withings si pri registrácii Callback URL
    overuje, či je adresa dostupná — request bez parametrov, čaká 200.

    Prepojenie sa uloží len vtedy, keď `state` vydal tento server a zároveň
    je prehliadač prihlásený ako ten istý používateľ. Bez druhej kontroly by
    útočník mohol obeti poslať svoj odkaz na súhlas a dáta obete z Withings
    by pritiekli do jeho účtu.
    """
    if error:
        return _back_to_app(withings="error", reason="denied")
    if not code:
        # Overovací request od Withings, nie skutočný callback.
        return {"status": "ready", "message": "Withings callback endpoint is reachable"}
    if not WITHINGS_AVAILABLE or get_withings_connector is None:
        return _back_to_app(withings="error", reason="unavailable")

    from app.integrations.connections import initial_sync, read_oauth_state

    state_user = read_oauth_state(state, "withings")
    cookie = request.cookies.get(settings.AUTH_COOKIE_NAME)
    session_user = decode_access_token(cookie) if cookie else None
    if state_user is None:
        return _back_to_app(withings="error", reason="expired")
    if session_user != state_user:
        logger.warning("[WITHINGS] callback: state for user %s, browser session %s — refused",
                       state_user, session_user)
        return _back_to_app(withings="error", reason="session")

    connector = get_withings_connector(state_user)
    if not await connector.exchange_code(code):
        return _back_to_app(withings="error", reason="exchange")

    background_tasks.add_task(initial_sync, state_user)
    return _back_to_app(withings="connected")


@router.get("/withings/sleep")
async def get_withings_sleep(days: int = 30, user: User = Depends(get_current_user)):
    """
    Spánok z hodiniek — fázy, prebúdzania, tep, dychové poruchy
    """
    return {"sleep": await _withings_call(_withings_for(user).get_sleep(days))}


@router.get("/withings/activity")
async def get_withings_activity(days: int = 30, user: User = Depends(get_current_user)):
    """
    Denná aktivita — kroky, vzdialenosť, poschodia, pásma tepu
    """
    return {"activity": await _withings_call(_withings_for(user).get_activity(days))}


@router.get("/withings/measures")
async def get_withings_measures(days: int = 30, user: User = Depends(get_current_user)):
    """
    Merania — tep, SpO2, teplota, VO2max, EKG intervaly
    """
    return {"measures": await _withings_call(_withings_for(user).get_measures(days))}


@router.get("/withings/hrv")
async def get_withings_hrv(nights: int = 14, user: User = Depends(get_current_user)):
    """
    Variabilita srdcovej frekvencie zo spánku (rmssd, sdnn_1).

    Pomalšie než ostatné endpointy - jedna noc je jeden request na Withings,
    lebo /v2/sleep s action=get má obmedzené časové okno.
    """
    return {"hrv": await _withings_call(_withings_for(user).get_sleep_hrv(nights))}


@router.get("/withings/ecg")
async def get_withings_ecg(with_signal: bool = False, user: User = Depends(get_current_user)):
    """
    EKG záznamy. with_signal=true stiahne aj surové krivky
    (9000 vzoriek na záznam, ~29 KB) — nepoužívaj pri každom načítaní stránky.
    """
    return {"ecg": await _withings_call(_withings_for(user).get_ecg(with_signal))}


@router.post("/withings/sync-db")
async def sync_withings_to_database(
    days: int = 30,
    hrv_nights: int = 14,
    user: User = Depends(get_current_user),
):
    """
    Zapíše Withings dáta používateľa do jeho health_records.

    Bez tohto kroku Withings vidí len stránka Withings Watch. Chat, Trendy,
    Riziká a modely čítajú výhradne cez app.analysis.sources, teda z databázy.
    Idempotentné — opakované spustenie riadky prepíše, nezduplikuje.
    """
    _withings_for(user)
    from app.integrations.connections import sync_withings
    return await _withings_call(sync_withings(user.id, days=days, hrv_nights=hrv_nights))


@router.post("/withings/sync")
async def sync_withings_data(request: SyncRequest, background_tasks: BackgroundTasks,
                             user: User = Depends(get_current_user)):
    """
    Synchronizovať historické dáta z Withings (na pozadí)
    """
    _withings_for(user)
    from app.integrations.connections import sync_withings
    background_tasks.add_task(sync_withings, user.id, request.days)
    return {
        "success": True,
        "message": f"Sync started for last {request.days} days",
        "status": "processing",
    }


@router.get("/calendar/auth")
async def authenticate_calendar(_admin=Depends(require_admin)):
    """
    Autentifikácia do Google Calendar
    """
    try:
        if not CALENDAR_AVAILABLE or get_calendar_connector is None:
            raise HTTPException(status_code=503, detail="Calendar integrácia nie je dostupná (chýba balík alebo závislosti).")
        connector = get_calendar_connector()
        success = connector.authenticate()
        
        if success:
            return {
                "success": True,
                "message": "Successfully authenticated to Google Calendar"
            }
        else:
            raise HTTPException(status_code=401, detail="Authentication failed")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calendar/events")
async def get_calendar_events(days_back: int = 30, days_forward: int = 7, _admin=Depends(require_admin)):
    """
    Získať udalosti z kalendára
    """
    try:
        if not CALENDAR_AVAILABLE or get_calendar_connector is None:
            raise HTTPException(status_code=503, detail="Calendar integrácia nie je dostupná (chýba balík alebo závislosti).")
        connector = get_calendar_connector()
        
        if not connector.is_authenticated:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated. Please authenticate first."
            )
        
        events = connector.get_events(days_back, days_forward)
        analysis = connector.analyze_event_categories(events)
        
        return {
            "events": events,
            "analysis": analysis
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze/correlations")
async def analyze_correlations(request: CorrelationAnalysisRequest, _admin=Depends(require_admin)):
    """
    Analyzovať korelácie medzi kalendárom a zdravotnými metrikami
    """
    try:
        if not GARMIN_AVAILABLE or get_garmin_connector is None:
            raise HTTPException(status_code=503, detail="Garmin integrácia nie je dostupná (chýba balík alebo závislosti).")
        if not CALENDAR_AVAILABLE or get_calendar_connector is None:
            raise HTTPException(status_code=503, detail="Calendar integrácia nie je dostupná (chýba balík alebo závislosti).")
        garmin = get_garmin_connector()
        calendar = get_calendar_connector()
        
        if not garmin.is_authenticated or not calendar.is_authenticated:
            raise HTTPException(
                status_code=401,
                detail="Both Garmin and Calendar must be authenticated"
            )
        
        # Získať dáta
        print(f"[CORRELATION] Analyzing correlations for last {request.days} days")
        
        garmin_data = await garmin.get_historical_data(request.days)
        calendar_events = calendar.get_events(days_back=request.days, days_forward=0)
        
        # Analyzovať korelácie
        correlations = _analyze_health_event_correlations(garmin_data, calendar_events)
        
        return correlations
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def sync_garmin_background(days: int):
    """Background task pre synchronizáciu Garmin dát"""
    try:
        if not GARMIN_AVAILABLE or get_garmin_connector is None:
            print("[GARMIN] Sync skipped: Garmin integrácia nie je dostupná.")
            return
        connector = get_garmin_connector()
        data = await connector.get_historical_data(days)
        
        # Uložiť do databázy/súboru
        from pathlib import Path
        import json
        
        data_dir = Path("data/garmin")
        data_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"garmin_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = data_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"[GARMIN] Sync completed. Saved to {filepath}")
    
    except Exception as e:
        print(f"[GARMIN ERROR] Background sync failed: {e}")


def _analyze_health_event_correlations(
    garmin_data: List[Dict],
    calendar_events: List[Dict]
) -> Dict[str, Any]:
    """
    Analyzuje korelácie medzi zdravotnými dátami a kalendárnymi udalosťami
    """
    correlations = {
        "high_stress_days": [],
        "poor_sleep_days": [],
        "low_activity_days": [],
        "event_impact": {
            "work_meetings": {"avg_stress": 0, "avg_sleep_hours": 0},
            "social_events": {"avg_stress": 0, "avg_sleep_hours": 0},
            "sport_events": {"avg_stress": 0, "avg_sleep_hours": 0},
        },
        "insights": []
    }
    
    # Vytvoríme mapu dátumov
    garmin_by_date = {g["date"]: g for g in garmin_data}
    
    # Analyzujeme každý deň
    for date, health_data in garmin_by_date.items():
        stress = health_data.get("stress", {})
        sleep = health_data.get("sleep", {})
        steps = health_data.get("steps", {})
        
        avg_stress = stress.get("avg_stress_level", 0)
        total_sleep = sleep.get("total_sleep_seconds", 0)
        total_steps = steps.get("total_steps", 0)
        
        # Nájdeme udalosti pre tento deň
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        day_events = [
            e for e in calendar_events 
            if datetime.fromisoformat(e["start"]).date() == date_obj.date()
        ]
        
        # Vysoký stres?
        if avg_stress > 60:
            correlations["high_stress_days"].append({
                "date": date,
                "stress_level": avg_stress,
                "events": len(day_events),
                "event_summaries": [e["summary"] for e in day_events[:3]]
            })
        
        # Zlý spánok?
        sleep_hours = total_sleep / 3600 if total_sleep else 0
        if sleep_hours > 0 and sleep_hours < 6:
            correlations["poor_sleep_days"].append({
                "date": date,
                "sleep_hours": round(sleep_hours, 1),
                "events": len(day_events),
                "event_summaries": [e["summary"] for e in day_events[:3]]
            })
        
        # Nízka aktivita?
        if total_steps > 0 and total_steps < 5000:
            correlations["low_activity_days"].append({
                "date": date,
                "steps": total_steps,
                "events": len(day_events),
                "event_summaries": [e["summary"] for e in day_events[:3]]
            })
    
    # Vytvoríme insights
    if correlations["high_stress_days"]:
        avg_events_on_stress_days = sum(d["events"] for d in correlations["high_stress_days"]) / len(correlations["high_stress_days"])
        correlations["insights"].append(
            f"Dni s vysokým stresom (>60) majú priemerne {avg_events_on_stress_days:.1f} udalostí v kalendári."
        )
    
    if correlations["poor_sleep_days"]:
        correlations["insights"].append(
            f"Zistených {len(correlations['poor_sleep_days'])} dní so zlým spánkom (<6h)."
        )
    
    if correlations["low_activity_days"]:
        correlations["insights"].append(
            f"Zistených {len(correlations['low_activity_days'])} dní s nízkou aktivitou (<5000 krokov)."
        )
    
    return correlations
