from typing import Dict, List, Optional
from datetime import datetime

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
            'test': 'Kompletný krvný obraz',
            'frequency': 'ročne',
            'priority': 'high',
            'description': 'Základné vyšetrenie krvi'
        })
        
        tests.append({
            'test': 'Lipidový profil',
            'frequency': 'ročne',
            'priority': 'high',
            'description': 'Cholesterol, LDL, HDL, triglyceridy'
        })
        
        tests.append({
            'test': 'Glykémia nalačno',
            'frequency': 'ročne',
            'priority': 'high',
            'description': 'Hladina cukru v krvi'
        })
        
        # Vyšetrenia podľa veku
        if age >= 40:
            tests.append({
                'test': 'HbA1c',
                'frequency': 'ročne',
                'priority': 'high',
                'description': 'Dlhodobá kontrola glykémie'
            })
            
            tests.append({
                'test': 'EKG',
                'frequency': 'ročne',
                'priority': 'medium',
                'description': 'Vyšetrenie srdca'
            })
        
        if age >= 45:
            tests.append({
                'test': 'Ergometria (záťažové EKG)',
                'frequency': '2 roky',
                'priority': 'medium',
                'description': 'Funkčná kapacita srdca'
            })
            
            tests.append({
                'test': 'Kolonoskopia',
                'frequency': '10 rokov',
                'priority': 'high',
                'description': 'Screening rakoviny hrubého čreva'
            })
        
        if age >= 50:
            tests.append({
                'test': 'PSA (muži)',
                'frequency': 'ročne',
                'priority': 'medium',
                'description': 'Screening rakoviny prostaty'
            })
            
            tests.append({
                'test': 'Mamografia (ženy)',
                'frequency': '2 roky',
                'priority': 'high',
                'description': 'Screening rakoviny prsníka'
            })
        
        if age >= 55:
            tests.append({
                'test': 'Denzitometria',
                'frequency': '2 roky',
                'priority': 'medium',
                'description': 'Meranie hustoty kostí'
            })
        
        return tests
    
    def _get_lifestyle_recommendations(self) -> List[Dict]:
        """Odporúčania pre zdravý životný štýl"""
        return [
            {
                'category': 'Výživa',
                'recommendations': [
                    'Mediteránska diéta s vysokým obsahom zeleniny',
                    'Obmedzenie červeného mäsa',
                    'Zvýšenie príjmu omega-3 mastných kyselín',
                    'Redukcia soli a cukru',
                    'Dostatočný príjem vlákniny'
                ]
            },
            {
                'category': 'Fyzická aktivita',
                'recommendations': [
                    'Minimálne 150 minút stredne intenzívnej aktivity týždenne',
                    'Silový tréning 2x týždenne',
                    'Denné prechádzky',
                    'Zníženie sedavého spôsobu života'
                ]
            },
            {
                'category': 'Životný štýl',
                'recommendations': [
                    'Dostatok spánku (7-9 hodín)',
                    'Manažment stresu',
                    'Vyhýbanie sa fajčeniu',
                    'Obmedzenie alkoholu',
                    'Pravidelné merane krvného tlaku doma'
                ]
            },
            {
                'category': 'Preventívne kontroly',
                'recommendations': [
                    'Pravidelné návštevy praktického lekára',
                    'Preventívne zubné kontroly',
                    'Očné vyšetrenia',
                    'Dermatologické kontroly'
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
            'Kompletný krvný obraz',
            'Lipidový profil',
            'Glykémia nalačno'
        ]
        
        # Do 3 mesiacov
        schedule['next_3_months'] = [
            'Kontrola krvného tlaku',
            'BMI a obvod pása'
        ]
        
        # Do 6 mesiacov
        if age >= 40:
            schedule['next_6_months'] = [
                'EKG',
                'Ultrazvuk brucha'
            ]
        
        # Ročné kontroly
        schedule['annual'] = [
            'Komplexná lekárska prehliadka',
            'Očné vyšetrenie',
            'Zubná kontrola'
        ]
        
        return schedule
