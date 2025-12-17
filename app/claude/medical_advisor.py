from typing import Dict, Optional
import anthropic
import os

from app.config import settings

class MedicalAdvisor:
    """Claude AI integrácia pre medicínsku analýzu"""
    
    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.client = None
        
        if self.api_key:
            self.client = anthropic.Anthropic(api_key=self.api_key)
    
    async def analyze_health_risks(self, ml_predictions: Dict) -> Dict:
        """
        Použije Claude AI pre pokročilú analýzu zdravotných rizík
        
        Args:
            ml_predictions: Výsledky z ML modelov
        """
        if not self.client:
            return {
                'available': False,
                'message': 'Claude AI analýza nie je dostupná. Pre aktiváciu pridajte ANTHROPIC_API_KEY do .env súboru.',
                'instructions': 'Získajte API kľúč na https://console.anthropic.com/'
            }
        
        try:
            # Pripraviť prompt pre Claude
            prompt = self._build_health_analysis_prompt(ml_predictions)
            
            # Zavolať Claude API
            message = self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=2000,
                temperature=0.3,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Extrahovať odpoveď
            analysis = message.content[0].text
            
            return {
                'ai_analysis': analysis,
                'model': 'claude-3-5-sonnet',
                'timestamp': message.id
            }
        
        except Exception as e:
            return {
                'error': str(e),
                'message': 'Failed to get AI analysis'
            }
    
    def _build_health_analysis_prompt(self, ml_predictions: Dict) -> str:
        """Vytvorí prompt pre Claude"""
        prompt = f"""Si skúsený lekár s expertízou v preventívnej medicíne. 
Analyzuj nasledujúce predikcie zdravotných rizík a poskytni:

1. Celkové zhodnotenie zdravotného stavu
2. Najvýznamnejšie riziká a ich priority
3. Konkrétne odporúčania pre pacienta
4. Preventívne opatrenia

ML Predikcie:
{self._format_predictions(ml_predictions)}

Prosím, poskytni analýzu v slovenčine, štruktúrovane a zrozumiteľne pre pacienta.
Zdôrazni preventívne aspekty a motivuj k zdravému životnému štýlu.
"""
        return prompt
    
    def _format_predictions(self, predictions: Dict) -> str:
        """Formátuje predikcie pre prompt"""
        formatted = []
        
        for disease, data in predictions.items():
            if isinstance(data, dict) and 'risk_level' in data:
                formatted.append(f"""
Ochorenie: {disease}
Úroveň rizika: {data.get('risk_level')}
Percentuálne riziko: {data.get('risk_percentage')}%
Rizikové faktory: {', '.join(data.get('factors', []))}
""")
        
        return '\n'.join(formatted)
    
    async def get_personalized_recommendations(
        self,
        patient_data: Dict,
        preferences: Optional[Dict] = None
    ) -> str:
        """Generuje personalizované odporúčania pomocou Claude"""
        if not self.client:
            return "Claude API not configured"
        
        prompt = f"""Vytvor personalizovaný plán zdravia pre pacienta s nasledujúcimi údajmi:

{patient_data}

Preferencie: {preferences or 'žiadne špecifikované'}

Vytvor:
1. Krátkodobý akčný plán (1-3 mesiace)
2. Strednodobé ciele (3-6 mesiacov)
3. Dlhodobú stratégiu (1+ rok)

Odpoveď v slovenčine, prakticky a motivujúco.
"""
        
        try:
            message = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            return message.content[0].text
        
        except Exception as e:
            return f"Error: {str(e)}"
