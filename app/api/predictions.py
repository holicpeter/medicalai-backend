from fastapi import APIRouter, HTTPException
from typing import Optional

from app.ml.risk_predictor import RiskPredictor
from app.ml.recommendation_engine import RecommendationEngine
from app.claude.medical_advisor import MedicalAdvisor

router = APIRouter()
risk_predictor = RiskPredictor()
recommendation_engine = RecommendationEngine()
medical_advisor = MedicalAdvisor()

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
            "high_risk_conditions": ml_risks.get("high_risk_conditions", [])
        }
        
        # Optional Claude AI analysis
        if use_claude:
            claude_analysis = await medical_advisor.analyze_health_risks(ml_risks)
            result["ai_insights"] = claude_analysis
        
        return result
    
    except Exception as e:
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
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/disease-risk/{disease}")
async def get_specific_disease_risk(disease: str):
    """
    Špecifická predikcia rizika pre konkrétne ochorenie
    
    Supported diseases: diabetes, cardiovascular, hypertension, metabolic_syndrome
    """
    try:
        supported_diseases = ["diabetes", "cardiovascular", "hypertension", "metabolic_syndrome"]
        
        if disease not in supported_diseases:
            raise HTTPException(
                status_code=400,
                detail=f"Disease must be one of: {supported_diseases}"
            )
        
        risk_assessment = risk_predictor.predict_disease_risk(disease)
        
        return {
            "disease": disease,
            "risk_level": risk_assessment.get("risk_level"),
            "risk_percentage": risk_assessment.get("risk_percentage"),
            "contributing_factors": risk_assessment.get("factors", []),
            "recommendations": risk_assessment.get("recommendations", [])
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
