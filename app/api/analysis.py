from fastapi import APIRouter, HTTPException
from typing import Optional
from datetime import datetime, timedelta

from app.analysis.trend_analyzer import TrendAnalyzer
from app.analysis.health_metrics import HealthMetricsAnalyzer

router = APIRouter()
trend_analyzer = TrendAnalyzer()
metrics_analyzer = HealthMetricsAnalyzer()

@router.get("/trends")
async def get_health_trends(
    metric: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    Analyzuje trendy v zdravotných ukazovateľoch
    
    Parameters:
    - metric: blood_pressure, glucose, cholesterol, bmi (None = všetky)
    - start_date: YYYY-MM-DD
    - end_date: YYYY-MM-DD
    """
    try:
        print(f"[API] /trends endpoint called: metric={metric}, start_date={start_date}, end_date={end_date}")
        
        trends = trend_analyzer.analyze_trends(
            metric=metric,
            start_date=start_date,
            end_date=end_date
        )
        
        print(f"[API] analyze_trends returned: {type(trends)}")
        
        # Ak trends je už kompletný objekt (s message), vráť ho tak ako je
        if isinstance(trends, dict) and "trends" not in trends:
            return {"trends": trends}
        
        summary = {}
        try:
            summary = trend_analyzer.get_summary(trends if isinstance(trends, dict) and "trends" not in trends else trends.get("trends", {}))
        except Exception as e:
            print(f"[API] Warning: Cannot generate summary: {e}")
        
        return {
            "trends": trends,
            "summary": summary
        }
    
    except Exception as e:
        print(f"[API ERROR] /trends failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics/latest")
async def get_latest_metrics():
    """Získa najnovšie zdravotné ukazovatele"""
    try:
        latest = metrics_analyzer.get_latest_metrics()
        return latest
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/metrics/history")
async def get_metrics_history(days: int = 365):
    """Získa históriu meraní za posledných N dní"""
    try:
        history = metrics_analyzer.get_metrics_history(days=days)
        return {
            "period_days": days,
            "metrics": history
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/summary")
async def get_health_summary():
    """Komplexný zdravotný prehľad"""
    try:
        summary = metrics_analyzer.get_comprehensive_summary()
        return summary
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/refresh-cache")
async def refresh_trend_cache():
    """Vymaže cache a znova načíta všetky dáta"""
    try:
        # Invalidovať cache
        from app.analysis.trend_analyzer import TrendAnalyzer
        TrendAnalyzer._data_cache = None
        TrendAnalyzer._cache_timestamp = None
        
        # Vytvoriť novú inštanciu aby sa dáta načítali znova
        new_analyzer = TrendAnalyzer()
        
        return {
            "success": True,
            "message": "Cache refreshed",
            "total_records": len(new_analyzer.data)
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
