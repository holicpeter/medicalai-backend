import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib
from typing import Dict, List
from pathlib import Path
import json

from app.config import settings

class RiskPredictor:
    """ML model pre predikciu zdravotných rizík"""
    
    def __init__(self):
        self.data = self._load_data()
        self.scaler = StandardScaler()
    
    def _load_data(self) -> pd.DataFrame:
        """Načíta zdravotné dáta"""
        all_metrics = []
        
        for json_file in settings.PROCESSED_DATA_DIR.glob("extracted_data_*.json"):
            with open(json_file, 'r', encoding='utf-8') as f:
                metrics = json.load(f)
                all_metrics.extend(metrics)
        
        if not all_metrics:
            return pd.DataFrame()
        
        return pd.DataFrame(all_metrics)
    
    def predict_risks(self) -> Dict:
        """Predikuje zdravotné riziká"""
        if self.data.empty:
            return {"error": "No data available for prediction"}
        
        # Pripraviť features
        features = self._prepare_features()
        
        if features is None:
            return {"error": "Insufficient data for risk prediction"}
        
        # Predikcia pre rôzne ochorenia
        risks = {
            'cardiovascular': self._predict_cardiovascular_risk(features),
            'diabetes': self._predict_diabetes_risk(features),
            'metabolic_syndrome': self._predict_metabolic_syndrome(features),
            'overall_risk_score': 0,
            'high_risk_conditions': []
        }
        
        # Vypočítať celkové riziko
        risk_scores = [v['risk_percentage'] for v in risks.values() if isinstance(v, dict) and 'risk_percentage' in v]
        if risk_scores:
            risks['overall_risk_score'] = round(np.mean(risk_scores), 1)
        
        # Identifikovať vysoké riziká
        for disease, risk_data in risks.items():
            if isinstance(risk_data, dict) and risk_data.get('risk_level') == 'high':
                risks['high_risk_conditions'].append(disease)
        
        return risks
    
    def predict_disease_risk(self, disease: str) -> Dict:
        """Predikuje riziko konkrétneho ochorenia"""
        features = self._prepare_features()
        
        if features is None:
            return {"error": "Insufficient data"}
        
        predictors = {
            'cardiovascular': self._predict_cardiovascular_risk,
            'diabetes': self._predict_diabetes_risk,
            'metabolic_syndrome': self._predict_metabolic_syndrome,
            'hypertension': self._predict_hypertension_risk
        }
        
        if disease in predictors:
            return predictors[disease](features)
        
        return {"error": f"Unknown disease: {disease}"}
    
    def _prepare_features(self) -> Dict:
        """Pripraví features pre ML model"""
        if self.data.empty:
            return None
        
        # Získať najnovšie hodnoty
        latest_metrics = {}
        
        for metric in self.data['metric'].unique():
            metric_data = self.data[self.data['metric'] == metric]
            if not metric_data.empty:
                latest_value = metric_data.iloc[-1]['value']
                latest_metrics[metric] = latest_value
        
        # Extract numerical features
        features = {}
        
        # Blood pressure
        if 'blood_pressure' in latest_metrics:
            bp = latest_metrics['blood_pressure']
            if isinstance(bp, dict):
                features['systolic'] = bp.get('systolic', 120)
                features['diastolic'] = bp.get('diastolic', 80)
        
        # Other metrics
        features['glucose'] = latest_metrics.get('glucose', 5.0)
        features['cholesterol'] = latest_metrics.get('cholesterol', 4.5)
        features['ldl'] = latest_metrics.get('ldl', 2.5)
        features['hdl'] = latest_metrics.get('hdl', 1.3)
        features['triglycerides'] = latest_metrics.get('triglycerides', 1.5)
        features['bmi'] = latest_metrics.get('bmi', 25.0)
        features['hba1c'] = latest_metrics.get('hba1c', 5.0)
        
        return features
    
    def _predict_cardiovascular_risk(self, features: Dict) -> Dict:
        """Framingham Risk Score pre kardiovaskulárne ochorenia"""
        risk_score = 0
        factors = []
        
        # Systolický tlak
        systolic = features.get('systolic', 120)
        if systolic >= 160:
            risk_score += 3
            factors.append("Vysoký systolický tlak")
        elif systolic >= 140:
            risk_score += 2
            factors.append("Mierne zvýšený systolický tlak")
        
        # LDL cholesterol
        ldl = features.get('ldl', 2.5)
        if ldl >= 4.0:
            risk_score += 2
            factors.append("Vysoký LDL cholesterol")
        elif ldl >= 3.0:
            risk_score += 1
            factors.append("Mierne zvýšený LDL cholesterol")
        
        # HDL cholesterol (nízky je rizikový)
        hdl = features.get('hdl', 1.3)
        if hdl < 1.0:
            risk_score += 2
            factors.append("Nízky HDL cholesterol")
        
        # BMI
        bmi = features.get('bmi', 25)
        if bmi >= 30:
            risk_score += 2
            factors.append("Obezita")
        elif bmi >= 25:
            risk_score += 1
            factors.append("Nadváha")
        
        # Vypočítať riziko
        risk_percentage = min(100, risk_score * 10)
        
        if risk_percentage >= 20:
            risk_level = "high"
        elif risk_percentage >= 10:
            risk_level = "medium"
        else:
            risk_level = "low"
        
        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': factors,
            'recommendations': self._get_cardiovascular_recommendations(risk_level)
        }
    
    def _predict_diabetes_risk(self, features: Dict) -> Dict:
        """Predikcia rizika diabetu"""
        risk_score = 0
        factors = []
        
        # HbA1c
        hba1c = features.get('hba1c', 5.0)
        if hba1c >= 6.5:
            risk_score += 4
            factors.append("Diabetické hodnoty HbA1c")
        elif hba1c >= 5.7:
            risk_score += 2
            factors.append("Prediabetické hodnoty HbA1c")
        
        # Glukóza nalačno
        glucose = features.get('glucose', 5.0)
        if glucose >= 7.0:
            risk_score += 3
            factors.append("Vysoká glukóza nalačno")
        elif glucose >= 5.6:
            risk_score += 2
            factors.append("Zvýšená glukóza nalačno")
        
        # BMI
        bmi = features.get('bmi', 25)
        if bmi >= 30:
            risk_score += 2
            factors.append("Obezita - rizikový faktor pre diabetes")
        
        risk_percentage = min(100, risk_score * 12)
        
        if risk_percentage >= 25:
            risk_level = "high"
        elif risk_percentage >= 15:
            risk_level = "medium"
        else:
            risk_level = "low"
        
        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': factors,
            'recommendations': self._get_diabetes_recommendations(risk_level)
        }
    
    def _predict_metabolic_syndrome(self, features: Dict) -> Dict:
        """Predikcia metabolického syndrómu"""
        criteria_met = 0
        factors = []
        
        # Kritériá metabolického syndrómu (3+ z 5)
        
        # 1. Obvod pása (použijeme BMI ako proxy)
        bmi = features.get('bmi', 25)
        if bmi >= 30:
            criteria_met += 1
            factors.append("Abdominálna obezita")
        
        # 2. Triglyceridy
        tg = features.get('triglycerides', 1.5)
        if tg >= 1.7:
            criteria_met += 1
            factors.append("Zvýšené triglyceridy")
        
        # 3. HDL cholesterol
        hdl = features.get('hdl', 1.3)
        if hdl < 1.0:
            criteria_met += 1
            factors.append("Nízky HDL cholesterol")
        
        # 4. Krvný tlak
        systolic = features.get('systolic', 120)
        diastolic = features.get('diastolic', 80)
        if systolic >= 130 or diastolic >= 85:
            criteria_met += 1
            factors.append("Zvýšený krvný tlak")
        
        # 5. Glukóza nalačno
        glucose = features.get('glucose', 5.0)
        if glucose >= 5.6:
            criteria_met += 1
            factors.append("Zvýšená glukóza nalačno")
        
        if criteria_met >= 3:
            risk_level = "high"
            risk_percentage = 80
        elif criteria_met == 2:
            risk_level = "medium"
            risk_percentage = 50
        else:
            risk_level = "low"
            risk_percentage = 20
        
        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'criteria_met': criteria_met,
            'factors': factors,
            'recommendations': self._get_metabolic_recommendations(risk_level)
        }
    
    def _predict_hypertension_risk(self, features: Dict) -> Dict:
        """Predikcia rizika hypertenzie"""
        systolic = features.get('systolic', 120)
        diastolic = features.get('diastolic', 80)
        
        if systolic >= 140 or diastolic >= 90:
            risk_level = "high"
            risk_percentage = 90
        elif systolic >= 130 or diastolic >= 80:
            risk_level = "medium"
            risk_percentage = 60
        else:
            risk_level = "low"
            risk_percentage = 20
        
        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': [f"Systolic: {systolic}, Diastolic: {diastolic}"],
            'recommendations': ["Pravidelné meranie tlaku", "Redukcia soli v strave"]
        }
    
    def _get_cardiovascular_recommendations(self, risk_level: str) -> List[str]:
        """Odporúčania pre kardiovaskulárne zdravie"""
        if risk_level == "high":
            return [
                "Urge konzultácia s kardiológom",
                "Pravidelné monitorovanie krvného tlaku",
                "Zvážte liečbu statínmi",
                "Úprava stravy - zníženie nasýtených tukov",
                "Zvýšená fyzická aktivita"
            ]
        elif risk_level == "medium":
            return [
                "Kontrola u kardiológa do 3 mesiacov",
                "Pravidelná aeróbna aktivita",
                "Zdravá strava s nízkym obsahom cholesterolu"
            ]
        return ["Udržujte zdravý životný štýl", "Pravidelné ročné kontroly"]
    
    def _get_diabetes_recommendations(self, risk_level: str) -> List[str]:
        """Odporúčania pre prevenciu diabetu"""
        if risk_level == "high":
            return [
                "Konzultácia s diabetológom",
                "Pravidelná kontrola glykémie",
                "Redukcia jednoduchých cukrov",
                "Zvýšenie fyzickej aktivity",
                "Kontrola telesnej hmotnosti"
            ]
        return ["Zdravá strava", "Pravidelná fyzická aktivita"]
    
    def _get_metabolic_recommendations(self, risk_level: str) -> List[str]:
        """Odporúčania pre metabolický syndróm"""
        if risk_level == "high":
            return [
                "Komplexná lekárska kontrola",
                "Redukcia telesnej hmotnosti",
                "Zvýšenie fyzickej aktivity na 150 min/týždeň",
                "Strava bohatá na vlákninu",
                "Pravidelné sledovanie metabolických parametrov"
            ]
        return ["Prevencia nadváhy", "Pravidelná pohybová aktivita"]
