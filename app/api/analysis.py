import logging
from fastapi import APIRouter, HTTPException
from typing import Optional

from app.analysis.trend_analyzer import TrendAnalyzer
from app.analysis.health_metrics import HealthMetricsAnalyzer

logger = logging.getLogger(__name__)

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
        logger.info('/trends called: metric=%s start=%s end=%s', metric, start_date, end_date)

        trends = trend_analyzer.analyze_trends(
            metric=metric,
            start_date=start_date,
            end_date=end_date,
        )

        logger.debug('analyze_trends returned type: %s', type(trends).__name__)

        if isinstance(trends, dict) and "trends" not in trends:
            return {"trends": trends}

        summary = {}
        try:
            summary = trend_analyzer.get_summary(
                trends if isinstance(trends, dict) and "trends" not in trends
                else trends.get("trends", {})
            )
        except Exception as e:
            logger.warning('Cannot generate summary: %s', e)

        return {"trends": trends, "summary": summary}

    except Exception as e:
        logger.exception('/trends failed')
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

@router.post("/refresh-cache")
async def refresh_trend_cache():
    """Vymaže cache a znova načíta všetky dáta"""
    try:
        TrendAnalyzer.invalidate_cache()

        # Refresh the analyzer the router actually serves from, rather than
        # building a throwaway one and leaving the old data in place.
        trend_analyzer.refresh()

        return {
            "success": True,
            "message": "Cache refreshed",
            "total_records": len(trend_analyzer.data),
        }

    except Exception as e:
        logger.exception('/refresh-cache failed')
        raise HTTPException(status_code=500, detail="Cache refresh failed")
