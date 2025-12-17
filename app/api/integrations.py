from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

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

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class GarminAuthRequest(BaseModel):
    email: str
    password: str


class SyncRequest(BaseModel):
    days: int = 30


class CorrelationAnalysisRequest(BaseModel):
    days: int = 30


@router.post("/garmin/auth")
async def authenticate_garmin(request: GarminAuthRequest):
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
async def get_garmin_daily_data(date: Optional[str] = None):
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
async def sync_garmin_data(request: SyncRequest, background_tasks: BackgroundTasks):
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


@router.get("/calendar/auth")
async def authenticate_calendar():
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
async def get_calendar_events(days_back: int = 30, days_forward: int = 7):
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
async def analyze_correlations(request: CorrelationAnalysisRequest):
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
