from typing import Dict, List, Optional
from datetime import datetime

from app.i18n import tr

class RecommendationEngine:
    """Generuje odporúčania pre preventívne vyšetrenia"""
    
    def generate_recommendations(self, age: Optional[int] = None) -> Dict:
        """
        Generuje personalizované odporúčania
        
        Args:
            age: Vek pacienta
        """
        if age is None:
            age = 40  # Default ak nevieme určiť vek
        
        recommendations = {
            'tests': self._get_recommended_tests(age),
            'lifestyle': self._get_lifestyle_recommendations(),
            'schedule': self._get_screening_schedule(age)
        }
        
        return recommendations
    
    def _get_recommended_tests(self, age: int) -> List[Dict]:
        """Odporúčané vyšetrenia podľa veku"""
        tests = []
        
        # Základné vyšetrenia pre všetkých
        tests.append({
            'test': tr('Kompletný krvný obraz', "Complete blood count"),
            'frequency': tr('ročne', "yearly"),
            'priority': 'high',
            'description': tr('Základné vyšetrenie krvi', "Basic blood test")
        })
        
        tests.append({
            'test': tr('Lipidový profil', "Lipid profile"),
            'frequency': tr('ročne', "yearly"),
            'priority': 'high',
            'description': tr('Cholesterol, LDL, HDL, triglyceridy', "Cholesterol, LDL, HDL, triglycerides")
        })
        
        tests.append({
            'test': tr('Glykémia nalačno', "Fasting blood glucose"),
            'frequency': tr('ročne', "yearly"),
            'priority': 'high',
            'description': tr('Hladina cukru v krvi', "Blood sugar level")
        })
        
        # Vyšetrenia podľa veku
        if age >= 40:
            tests.append({
                'test': 'HbA1c',
                'frequency': tr('ročne', "yearly"),
                'priority': 'high',
                'description': tr('Dlhodobá kontrola glykémie', "Long-term blood sugar control")
            })
            
            tests.append({
                'test': 'EKG',
                'frequency': tr('ročne', "yearly"),
                'priority': 'medium',
                'description': tr('Vyšetrenie srdca', "Heart check")
            })
        
        if age >= 45:
            tests.append({
                'test': tr('Ergometria (záťažové EKG)', "Exercise stress test (stress ECG)"),
                'frequency': tr('2 roky', "every 2 years"),
                'priority': 'medium',
                'description': tr('Funkčná kapacita srdca', "Functional capacity of the heart")
            })
            
            tests.append({
                'test': tr('Kolonoskopia', "Colonoscopy"),
                'frequency': tr('10 rokov', "every 10 years"),
                'priority': 'high',
                'description': tr('Screening rakoviny hrubého čreva', "Colorectal cancer screening")
            })
        
        if age >= 50:
            tests.append({
                'test': tr('PSA (muži)', "PSA (men)"),
                'frequency': tr('ročne', "yearly"),
                'priority': 'medium',
                'description': tr('Screening rakoviny prostaty', "Prostate cancer screening")
            })
            
            tests.append({
                'test': tr('Mamografia (ženy)', "Mammography (women)"),
                'frequency': tr('2 roky', "every 2 years"),
                'priority': 'high',
                'description': tr('Screening rakoviny prsníka', "Breast cancer screening")
            })
        
        if age >= 55:
            tests.append({
                'test': tr('Denzitometria', "Bone densitometry"),
                'frequency': tr('2 roky', "every 2 years"),
                'priority': 'medium',
                'description': tr('Meranie hustoty kostí', "Bone density measurement")
            })
        
        return tests
    
    def _get_lifestyle_recommendations(self) -> List[Dict]:
        """Odporúčania pre zdravý životný štýl"""
        return [
            {
                'category': tr('Výživa', "Nutrition"),
                'recommendations': [
                    tr('Mediteránska diéta s vysokým obsahom zeleniny', "A Mediterranean diet rich in vegetables"),
                    tr('Obmedzenie červeného mäsa', "Less red meat"),
                    tr('Zvýšenie príjmu omega-3 mastných kyselín', "More omega-3 fatty acids"),
                    tr('Redukcia soli a cukru', "Less salt and sugar"),
                    tr('Dostatočný príjem vlákniny', "Enough fibre")
                ]
            },
            {
                'category': tr('Fyzická aktivita', "Physical activity"),
                'recommendations': [
                    tr('Minimálne 150 minút stredne intenzívnej aktivity týždenne', "At least 150 minutes of moderate activity a week"),
                    tr('Silový tréning 2x týždenne', "Strength training twice a week"),
                    tr('Denné prechádzky', "Daily walks"),
                    tr('Zníženie sedavého spôsobu života', "Sit less")
                ]
            },
            {
                'category': tr('Životný štýl', "Lifestyle"),
                'recommendations': [
                    tr('Dostatok spánku (7-9 hodín)', "Enough sleep (7-9 hours)"),
                    tr('Manažment stresu', "Stress management"),
                    tr('Vyhýbanie sa fajčeniu', "Avoid smoking"),
                    tr('Obmedzenie alkoholu', "Limit alcohol"),
                    tr('Pravidelné merane krvného tlaku doma', "Regular blood pressure checks at home")
                ]
            },
            {
                'category': tr('Preventívne kontroly', "Preventive check-ups"),
                'recommendations': [
                    tr('Pravidelné návštevy praktického lekára', "Regular visits to your GP"),
                    tr('Preventívne zubné kontroly', "Preventive dental check-ups"),
                    tr('Očné vyšetrenia', "Eye examinations"),
                    tr('Dermatologické kontroly', "Skin check-ups")
                ]
            }
        ]
    
    def _get_screening_schedule(self, age: int) -> Dict:
        """Navrhuje harmonogram preventívnych vyšetrení"""
        schedule = {
            'immediate': [],
            'next_3_months': [],
            'next_6_months': [],
            'annual': []
        }
        
        # Okamžité vyšetrenia (ak neboli vykonané v posledných 6 mesiacoch)
        schedule['immediate'] = [
            tr('Kompletný krvný obraz', "Complete blood count"),
            tr('Lipidový profil', "Lipid profile"),
            tr('Glykémia nalačno', "Fasting blood glucose")
        ]
        
        # Do 3 mesiacov
        schedule['next_3_months'] = [
            tr('Kontrola krvného tlaku', "Blood pressure check"),
            tr('BMI a obvod pása', "BMI and waist circumference")
        ]
        
        # Do 6 mesiacov
        if age >= 40:
            schedule['next_6_months'] = [
                'EKG',
                tr('Ultrazvuk brucha', "Abdominal ultrasound")
            ]
        
        # Ročné kontroly
        schedule['annual'] = [
            tr('Komplexná lekárska prehliadka', "Comprehensive medical check-up"),
            tr('Očné vyšetrenie', "Eye examination"),
            tr('Zubná kontrola', "Dental check-up")
        ]
        
        return schedule
