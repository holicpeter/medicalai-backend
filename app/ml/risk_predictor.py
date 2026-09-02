import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app.analysis.trend_analyzer import TrendAnalyzer

logger = logging.getLogger(__name__)


class RiskPredictor:
    """ML model pre predikciu zdravotných rizík"""

    def __init__(self):
        # The trend analyzer is the single place that knows how to load every
        # stored source — legacy JSON exports, manual and OCR records, Apple
        # Health — and it keeps a shared cache that every write path already
        # invalidates. Reading through it is why the risks screen can no
        # longer disagree with /analysis/trends about what data exists: this
        # class used to read only extracted_data_*.json off local disk, which
        # is empty on the deployed instance, so every risk came back zero.
        self._analyzer = TrendAnalyzer()
        self.refresh()

    def refresh(self):
        """Reload the measurements before predicting.

        This has to run per request, not once in __init__. The router holds a
        single module-level predictor, so data read at startup stayed frozen
        for the life of the process and nothing imported afterwards ever
        reached the risks screen.
        """
        self._analyzer.refresh()
        self.data = self._analyzer.data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _empty_risks(self) -> Dict:
        """Shape-compatible result for an account with no data yet."""
        return {
            'cardiovascular': None,
            'diabetes': None,
            'metabolic_syndrome': None,
            'overall_risk_score': 0,
            'high_risk_conditions': [],
            'has_data': False,
        }

    def predict_risks(self) -> Dict:
        """Predikuje zdravotné riziká"""
        self.refresh()

        if self.data.empty:
            return self._empty_risks()

        features = self._prepare_features()

        if not features:
            return self._empty_risks()

        risks = {
            'cardiovascular': self._predict_cardiovascular_risk(features),
            'diabetes': self._predict_diabetes_risk(features),
            'metabolic_syndrome': self._predict_metabolic_syndrome(features),
            'overall_risk_score': 0,
            'high_risk_conditions': [],
        }

        assessments = [v for v in risks.values() if isinstance(v, dict)]

        # Only diseases we could actually assess count towards the overall
        # score. Averaging in a nominal 0 for a disease whose inputs are all
        # missing would read as "healthy" rather than "unknown".
        risk_scores = [
            a['risk_percentage'] for a in assessments
            if a.get('risk_level') != 'unknown'
        ]
        if risk_scores:
            risks['overall_risk_score'] = round(float(np.mean(risk_scores)), 1)

        risks['high_risk_conditions'] = [
            disease for disease, assessment in risks.items()
            if isinstance(assessment, dict) and assessment.get('risk_level') == 'high'
        ]

        risks['has_data'] = True
        risks['data_complete'] = all(a.get('data_complete') for a in assessments)
        risks['measured_metrics'] = sorted(features.keys())
        return risks

    def predict_disease_risk(self, disease: str) -> Dict:
        """Predikuje riziko konkrétneho ochorenia"""
        self.refresh()

        predictors = {
            'cardiovascular': (self._predict_cardiovascular_risk, self._CARDIOVASCULAR_METRICS),
            'diabetes': (self._predict_diabetes_risk, self._DIABETES_METRICS),
            'metabolic_syndrome': (self._predict_metabolic_syndrome, self._METABOLIC_METRICS),
            'hypertension': (self._predict_hypertension_risk, self._HYPERTENSION_METRICS),
        }

        if disease not in predictors:
            return {"error": f"Unknown disease: {disease}"}

        predictor, needed = predictors[disease]
        features = self._prepare_features()

        if not features:
            # No data is a normal state for a fresh account, not a failure.
            # Returning {"error": ...} here made the endpoint answer with null
            # risk_level and risk_percentage, which the UI rendered as blank.
            return self._unknown_risk(needed)

        return predictor(features)

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _latest_values(self) -> Dict:
        """Most recent stored value per metric, by measurement date."""
        if self.data.empty or 'metric' not in self.data.columns:
            return {}

        df = self.data
        if 'date' in df.columns:
            # Rows arrive from several sources in no particular order, so
            # "latest" has to come from the date column rather than from
            # whichever row happens to sit at the end of the frame.
            df = df.dropna(subset=['date']).sort_values('date')

        latest = {}
        for metric in df['metric'].unique():
            rows = df[df['metric'] == metric]
            if rows.empty:
                continue
            value = rows.iloc[-1]['value']
            if isinstance(value, dict):
                latest[metric] = value
            elif value is not None and not pd.isna(value):
                latest[metric] = float(value)
        return latest

    @staticmethod
    def _bmi_from_body_measures(latest: Dict) -> Optional[float]:
        """BMI from weight and height when it was not measured directly.

        Apple Health exports weight and height far more often than BMI, and
        BMI feeds three of the risk models.
        """
        weight = latest.get('weight')      # canonical unit: kg
        height = latest.get('height')      # canonical unit: cm
        if not isinstance(weight, (int, float)) or not isinstance(height, (int, float)):
            return None
        if height <= 0:
            return None
        return round(weight / ((height / 100.0) ** 2), 1)

    def _prepare_features(self) -> Optional[Dict]:
        """Pripraví features pre ML model.

        Only measured values end up in the result. The old version filled every
        gap with a healthy-looking default (BMI 25, glucose 5.0, …), so an
        account with no lab results was scored as if it had normal ones — and
        the BMI default alone was enough to report "nadváha" as a contributing
        factor for someone who had never entered a weight.
        """
        latest = self._latest_values()
        if not latest:
            return None

        features: Dict[str, float] = {}

        bp = latest.get('blood_pressure')
        if isinstance(bp, dict):
            if bp.get('systolic') is not None:
                features['systolic'] = float(bp['systolic'])
            if bp.get('diastolic') is not None:
                features['diastolic'] = float(bp['diastolic'])

        # Apple Health stores the two halves as separate metrics; without this
        # every blood-pressure criterion was silently skipped for imported data.
        for half, metric in (('systolic', 'blood_pressure_systolic'),
                             ('diastolic', 'blood_pressure_diastolic')):
            if half not in features and isinstance(latest.get(metric), (int, float)):
                features[half] = float(latest[metric])

        for metric in ('glucose', 'cholesterol', 'ldl', 'hdl',
                       'triglycerides', 'bmi', 'hba1c'):
            value = latest.get(metric)
            if isinstance(value, (int, float)):
                features[metric] = float(value)

        if 'bmi' not in features:
            derived = self._bmi_from_body_measures(latest)
            if derived is not None:
                features['bmi'] = derived

        return features or None

    # ------------------------------------------------------------------
    # Shared assessment helpers
    # ------------------------------------------------------------------

    _CARDIOVASCULAR_METRICS = ['systolic', 'ldl', 'hdl', 'bmi']
    _DIABETES_METRICS = ['hba1c', 'glucose', 'bmi']
    _METABOLIC_METRICS = ['bmi', 'triglycerides', 'hdl', 'systolic', 'diastolic', 'glucose']
    _HYPERTENSION_METRICS = ['systolic', 'diastolic']

    @staticmethod
    def _coverage(features: Dict, needed: List[str]) -> Dict:
        measured = [m for m in needed if features.get(m) is not None]
        missing = [m for m in needed if features.get(m) is None]
        return {
            'evaluated_metrics': measured,
            'missing_metrics': missing,
            'data_complete': not missing,
        }

    @staticmethod
    def _unknown_risk(needed: List[str]) -> Dict:
        """Assessment for a disease none of whose inputs were measured."""
        return {
            'risk_level': 'unknown',
            'risk_percentage': 0,
            'factors': [],
            'recommendations': ["Zadajte namerané hodnoty pre výpočet rizika"],
            'evaluated_metrics': [],
            'missing_metrics': list(needed),
            'data_complete': False,
        }

    @staticmethod
    def _level(risk_percentage: float, high: float, medium: float) -> str:
        if risk_percentage >= high:
            return "high"
        if risk_percentage >= medium:
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Disease models
    # ------------------------------------------------------------------

    def _predict_cardiovascular_risk(self, features: Dict) -> Dict:
        """Framingham Risk Score pre kardiovaskulárne ochorenia"""
        coverage = self._coverage(features, self._CARDIOVASCULAR_METRICS)
        if not coverage['evaluated_metrics']:
            return self._unknown_risk(self._CARDIOVASCULAR_METRICS)

        risk_score = 0
        factors = []

        systolic = features.get('systolic')
        if systolic is not None:
            if systolic >= 160:
                risk_score += 3
                factors.append("Vysoký systolický tlak")
            elif systolic >= 140:
                risk_score += 2
                factors.append("Mierne zvýšený systolický tlak")

        ldl = features.get('ldl')
        if ldl is not None:
            if ldl >= 4.0:
                risk_score += 2
                factors.append("Vysoký LDL cholesterol")
            elif ldl >= 3.0:
                risk_score += 1
                factors.append("Mierne zvýšený LDL cholesterol")

        # HDL cholesterol (nízky je rizikový)
        hdl = features.get('hdl')
        if hdl is not None and hdl < 1.0:
            risk_score += 2
            factors.append("Nízky HDL cholesterol")

        bmi = features.get('bmi')
        if bmi is not None:
            if bmi >= 30:
                risk_score += 2
                factors.append("Obezita")
            elif bmi >= 25:
                risk_score += 1
                factors.append("Nadváha")

        risk_percentage = min(100, risk_score * 10)
        risk_level = self._level(risk_percentage, high=20, medium=10)

        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': factors,
            'recommendations': self._get_cardiovascular_recommendations(risk_level),
            **coverage,
        }

    def _predict_diabetes_risk(self, features: Dict) -> Dict:
        """Predikcia rizika diabetu"""
        coverage = self._coverage(features, self._DIABETES_METRICS)
        if not coverage['evaluated_metrics']:
            return self._unknown_risk(self._DIABETES_METRICS)

        risk_score = 0
        factors = []

        hba1c = features.get('hba1c')
        if hba1c is not None:
            if hba1c >= 6.5:
                risk_score += 4
                factors.append("Diabetické hodnoty HbA1c")
            elif hba1c >= 5.7:
                risk_score += 2
                factors.append("Prediabetické hodnoty HbA1c")

        glucose = features.get('glucose')
        if glucose is not None:
            if glucose >= 7.0:
                risk_score += 3
                factors.append("Vysoká glukóza nalačno")
            elif glucose >= 5.6:
                risk_score += 2
                factors.append("Zvýšená glukóza nalačno")

        bmi = features.get('bmi')
        if bmi is not None and bmi >= 30:
            risk_score += 2
            factors.append("Obezita - rizikový faktor pre diabetes")

        risk_percentage = min(100, risk_score * 12)
        risk_level = self._level(risk_percentage, high=25, medium=15)

        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': factors,
            'recommendations': self._get_diabetes_recommendations(risk_level),
            **coverage,
        }

    def _predict_metabolic_syndrome(self, features: Dict) -> Dict:
        """Predikcia metabolického syndrómu (3+ z 5 kritérií)"""
        coverage = self._coverage(features, self._METABOLIC_METRICS)
        if not coverage['evaluated_metrics']:
            return self._unknown_risk(self._METABOLIC_METRICS)

        criteria_met = 0
        criteria_evaluated = 0
        factors = []

        # 1. Obvod pása (použijeme BMI ako proxy)
        bmi = features.get('bmi')
        if bmi is not None:
            criteria_evaluated += 1
            if bmi >= 30:
                criteria_met += 1
                factors.append("Abdominálna obezita")

        # 2. Triglyceridy
        tg = features.get('triglycerides')
        if tg is not None:
            criteria_evaluated += 1
            if tg >= 1.7:
                criteria_met += 1
                factors.append("Zvýšené triglyceridy")

        # 3. HDL cholesterol
        hdl = features.get('hdl')
        if hdl is not None:
            criteria_evaluated += 1
            if hdl < 1.0:
                criteria_met += 1
                factors.append("Nízky HDL cholesterol")

        # 4. Krvný tlak
        systolic = features.get('systolic')
        diastolic = features.get('diastolic')
        if systolic is not None or diastolic is not None:
            criteria_evaluated += 1
            if (systolic is not None and systolic >= 130) or \
               (diastolic is not None and diastolic >= 85):
                criteria_met += 1
                factors.append("Zvýšený krvný tlak")

        # 5. Glukóza nalačno
        glucose = features.get('glucose')
        if glucose is not None:
            criteria_evaluated += 1
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
            'criteria_evaluated': criteria_evaluated,
            'factors': factors,
            'recommendations': self._get_metabolic_recommendations(risk_level),
            **coverage,
        }

    def _predict_hypertension_risk(self, features: Dict) -> Dict:
        """Predikcia rizika hypertenzie"""
        coverage = self._coverage(features, self._HYPERTENSION_METRICS)
        if not coverage['evaluated_metrics']:
            return self._unknown_risk(self._HYPERTENSION_METRICS)

        systolic = features.get('systolic')
        diastolic = features.get('diastolic')

        if (systolic is not None and systolic >= 140) or \
           (diastolic is not None and diastolic >= 90):
            risk_level = "high"
            risk_percentage = 90
        elif (systolic is not None and systolic >= 130) or \
             (diastolic is not None and diastolic >= 80):
            risk_level = "medium"
            risk_percentage = 60
        else:
            risk_level = "low"
            risk_percentage = 20

        measured = ", ".join(
            f"{label}: {value:g}"
            for label, value in (("Systolic", systolic), ("Diastolic", diastolic))
            if value is not None
        )

        return {
            'risk_level': risk_level,
            'risk_percentage': risk_percentage,
            'factors': [measured] if measured else [],
            'recommendations': ["Pravidelné meranie tlaku", "Redukcia soli v strave"],
            **coverage,
        }

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def _get_cardiovascular_recommendations(self, risk_level: str) -> List[str]:
        """Odporúčania pre kardiovaskulárne zdravie"""
        if risk_level == "high":
            return [
                "Urgentná konzultácia s kardiológom",
                "Pravidelné monitorovanie krvného tlaku",
                "Zvážte liečbu statínmi",
                "Úprava stravy - zníženie nasýtených tukov",
                "Zvýšená fyzická aktivita",
            ]
        elif risk_level == "medium":
            return [
                "Kontrola u kardiológa do 3 mesiacov",
                "Pravidelná aeróbna aktivita",
                "Zdravá strava s nízkym obsahom cholesterolu",
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
                "Kontrola telesnej hmotnosti",
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
                "Pravidelné sledovanie metabolických parametrov",
            ]
        return ["Prevencia nadváhy", "Pravidelná pohybová aktivita"]
