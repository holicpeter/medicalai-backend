import logging
from typing import Dict, Optional
import anthropic

from app.config import settings

logger = logging.getLogger(__name__)

_ANALYSIS_SYSTEM_PROMPT = (
    "Si skúsený lekár s expertízou v preventívnej medicíne. "
    "Analyzuj predikcie zdravotných rizík a poskytni štruktúrovanú analýzu v slovenčine. "
    "Zdôrazni preventívne aspekty a motivuj k zdravému životnému štýlu. "
    "Nikdy nediagnostikuj - len informuj o rizikách a odporúčaniach."
)


class MedicalAdvisor:
    """Claude AI integrácia pre medicínsku analýzu"""

    def __init__(self):
        self.client = (
            anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            if settings.ANTHROPIC_API_KEY
            else None
        )

    async def analyze_health_risks(self, ml_predictions: Dict) -> Dict:
        """Použije Claude AI pre pokročilú analýzu zdravotných rizík"""
        if not self.client:
            return {
                'available': False,
                'message': 'Claude AI analýza nie je dostupná. Pre aktiváciu pridajte ANTHROPIC_API_KEY do .env súboru.',
                'instructions': 'Získajte API kľúč na https://console.anthropic.com/',
            }

        try:
            user_prompt = (
                "Analyzuj nasledujúce predikcie zdravotných rizík a poskytni:\n\n"
                "1. Celkové zhodnotenie zdravotného stavu\n"
                "2. Najvýznamnejšie riziká a ich priority\n"
                "3. Konkrétne odporúčania pre pacienta\n"
                "4. Preventívne opatrenia\n\n"
                f"ML Predikcie:\n{self._format_predictions(ml_predictions)}"
            )

            message = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2000,
                system=[
                    {
                        "type": "text",
                        "text": _ANALYSIS_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )

            return {
                'ai_analysis': message.content[0].text,
                'model': 'claude-haiku-4-5-20251001',
                'message_id': message.id,
            }

        except Exception as e:
            logger.error('Claude health risk analysis failed: %s', e)
            return {
                'error': str(e),
                'message': 'Failed to get AI analysis',
            }

    def _format_predictions(self, predictions: Dict) -> str:
        """Formátuje predikcie pre prompt"""
        lines = []
        for disease, data in predictions.items():
            if isinstance(data, dict) and 'risk_level' in data:
                lines.append(
                    f"Ochorenie: {disease}\n"
                    f"Úroveň rizika: {data.get('risk_level')}\n"
                    f"Percentuálne riziko: {data.get('risk_percentage')}%\n"
                    f"Rizikové faktory: {', '.join(data.get('factors', []))}"
                )
        return '\n\n'.join(lines)

    async def get_personalized_recommendations(
        self,
        patient_data: Dict,
        preferences: Optional[Dict] = None,
    ) -> str:
        """Generuje personalizované odporúčania pomocou Claude"""
        if not self.client:
            return "Claude API not configured"

        prompt = (
            f"Vytvor personalizovaný plán zdravia pre pacienta s nasledujúcimi údajmi:\n\n"
            f"{patient_data}\n\n"
            f"Preferencie: {preferences or 'žiadne špecifikované'}\n\n"
            "Vytvor:\n"
            "1. Krátkodobý akčný plán (1-3 mesiace)\n"
            "2. Strednodobé ciele (3-6 mesiacov)\n"
            "3. Dlhodobú stratégiu (1+ rok)\n\n"
            "Odpoveď v slovenčine, prakticky a motivujúco."
        )

        try:
            message = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text

        except Exception as e:
            logger.error('Claude personalized recommendations failed: %s', e)
            return f"Error: {str(e)}"
