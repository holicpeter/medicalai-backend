import logging

from fastapi import APIRouter, HTTPException
from typing import Optional

from app.ml.risk_predictor import RiskPredictor
from app.ml.recommendation_engine import RecommendationEngine
from app.claude.medical_advisor import MedicalAdvisor

logger = logging.getLogger(__name__)

router = APIRouter()
risk_predictor = RiskPredictor()
recommendation_engine = RecommendationEngine()
medical_advisor = MedicalAdvisor()

SUPPORTED_DISEASES = ["diabetes", "cardiovascular", "hypertension", "metabolic_syndrome"]


@router.get("/risks")
async def predict_health_risks(use_claude: bool = False):
    """
    Predikcia budúcich zdravotných rizík

    Parameters:
    - use_claude: Použiť Claude AI pre pokročilú analýzu (vyžaduje API key)
    """
    try:
        # ML-based risk prediction
        ml_risks = risk_predictor.predict_risks()

        result = {
            "ml_predictions": ml_risks,
            "risk_score": ml_risks.get("overall_risk_score", 0),
            "high_risk_conditions": ml_risks.get("high_risk_conditions", []),
            # The UI needs to tell "no measurements yet" apart from "measured
            # and low risk"; both used to arrive as a score of 0.
            "has_data": ml_risks.get("has_data", False),
            "data_complete": ml_risks.get("data_complete", False),
            "measured_metrics": ml_risks.get("measured_metrics", []),
        }

        # Optional Claude AI analysis
        if use_claude:
            claude_analysis = await medical_advisor.analyze_health_risks(ml_risks)
            result["ai_insights"] = claude_analysis

        return result

    except Exception as e:
        logger.exception('/predictions/risks failed')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations")
async def get_preventive_recommendations(age: Optional[int] = None):
    """
    Odporúčania pre preventívne vyšetrenia

    Parameters:
    - age: Vek pacienta (ak nie je zadaný, vypočíta sa z dát)
    """
    try:
        recommendations = recommendation_engine.generate_recommendations(age=age)

        return {
            "preventive_tests": recommendations.get("tests", []),
            "lifestyle_recommendations": recommendations.get("lifestyle", []),
            "follow_up_schedule": recommendations.get("schedule", {})
        }

    except Exception as e:
        logger.exception('/predictions/recommendations failed')
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/disease-risk/{disease}")
async def get_specific_disease_risk(disease: str):
    """
    Špecifická predikcia rizika pre konkrétne ochorenie

    Supported diseases: diabetes, cardiovascular, hypertension, metabolic_syndrome
    """
    if disease not in SUPPORTED_DISEASES:
        # Raised outside the try block on purpose: catching Exception below
        # also catches HTTPException, so this 400 used to be re-raised as a
        # 500 with "400: Disease must be one of ..." as its detail.
        raise HTTPException(
            status_code=400,
            detail=f"Disease must be one of: {SUPPORTED_DISEASES}"
        )

    try:
        risk_assessment = risk_predictor.predict_disease_risk(disease)

        return {
            "disease": disease,
            "risk_level": risk_assessment.get("risk_level"),
            "risk_percentage": risk_assessment.get("risk_percentage"),
            "contributing_factors": risk_assessment.get("factors", []),
            "recommendations": risk_assessment.get("recommendations", []),
            "measured_metrics": risk_assessment.get("evaluated_metrics", []),
            "missing_metrics": risk_assessment.get("missing_metrics", []),
            "data_complete": risk_assessment.get("data_complete", False),
        }

    except Exception as e:
        logger.exception('/predictions/disease-risk/%s failed', disease)
        raise HTTPException(status_code=500, detail=str(e))
