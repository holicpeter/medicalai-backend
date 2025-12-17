from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import anthropic
from app.config import settings

try:
    from mistralai.client import MistralClient
except Exception:
    MistralClient = None

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    health_data: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    answer: str


@router.post("/ask", response_model=ChatResponse)
async def ask_question(request: ChatRequest):
    """
    Spracuje otázku používateľa a vráti odpoveď založenú na zdravotných dátach
    """
    try:
        # Pripravíme kontext zo zdravotných dát
        context = _prepare_health_context(request.health_data)
        
        # Vytvoríme prompt pre Claude AI
        system_prompt = """Si odborný zdravotný asistent s hlbokými znalosťami medicíny. 
Tvoja úloha je odpovedať na otázky pacienta o jeho zdravotných výsledkoch.

DÔLEŽITÉ PRAVIDLÁ:
- Odpovedaj VÝHRADNE v slovenskom jazyku
- Buď presný, faktický a opieraj sa len o poskytnuté dáta
- Ak nemáš dostatok informácií, oznám to pacientovi
- Nikdy nediagnostikuj choroby - len informuj o hodnotách a trendoch
- Odporúčaj konzultáciu s lekárom pri akýchkoľvek abnormálnych hodnotách
- Buď empatický a zrozumiteľný
- Vysvetľuj medicínske pojmy jednoducho"""

        user_prompt = f"""ZDRAVOTNÉ DÁTA PACIENTA:
{context}

OTÁZKA PACIENTA:
{request.question}

Prosím, odpovedz na túto otázku na základe poskytnutých zdravotných dát."""

        # Prefer Mistral, fallback na Claude
        if settings.MISTRAL_API_KEY and MistralClient is not None:
            client = MistralClient(api_key=settings.MISTRAL_API_KEY)
            response = client.chat(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
            )
            answer = response.choices[0].message.content
        elif settings.ANTHROPIC_API_KEY:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            message = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=2048,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            )
            answer = message.content[0].text
        else:
            raise HTTPException(status_code=500, detail="Chýba API kľúč pre Mistral alebo Claude. Pridaj MISTRAL_API_KEY alebo ANTHROPIC_API_KEY do .env")
        
        return ChatResponse(answer=answer)
        
    except Exception as e:
        print(f"[CHAT ERROR] {str(e)}")
        raise HTTPException(status_code=500, detail=f"Chyba pri spracovaní otázky: {str(e)}")


def _prepare_health_context(health_data: Optional[Dict[str, Any]]) -> str:
    """Pripraví prehľadný kontext zo zdravotných dát"""
    
    if not health_data:
        return "Žiadne zdravotné dáta nie sú momentálne dostupné."
    
    context_parts = []
    
    # Metriky
    if "metrics" in health_data and health_data["metrics"]:
        context_parts.append("=== ZDRAVOTNÉ METRIKY ===")
        
        # Zoskupíme metriky podľa typu
        metrics_by_type = {}
        for metric in health_data["metrics"]:
            metric_type = metric.get("type", "unknown")
            if metric_type not in metrics_by_type:
                metrics_by_type[metric_type] = []
            metrics_by_type[metric_type].append(metric)
        
        # Výpis metrik
        for metric_type, metrics in metrics_by_type.items():
            context_parts.append(f"\n{metric_type.upper().replace('_', ' ')}:")
            
            # Zoradíme podľa dátumu
            sorted_metrics = sorted(
                metrics, 
                key=lambda x: x.get("date", ""), 
                reverse=True
            )
            
            for metric in sorted_metrics[:5]:  # Max 5 najnovších hodnôt pre každý typ
                date = metric.get("date", "N/A")
                value = metric.get("value", "N/A")
                unit = metric.get("unit", "")
                context_parts.append(f"  - {date}: {value} {unit}")
    
    # Analýza a trendy
    if "analysis" in health_data and health_data["analysis"]:
        analysis = health_data["analysis"]
        
        if "trends" in analysis:
            context_parts.append("\n=== TRENDY ===")
            for trend in analysis["trends"]:
                metric_name = trend.get("metric", "Unknown")
                direction = trend.get("trend", "stable")
                context_parts.append(f"  - {metric_name}: {direction}")
        
        if "warnings" in analysis:
            context_parts.append("\n=== VAROVANIA ===")
            for warning in analysis["warnings"]:
                context_parts.append(f"  - {warning}")
        
        if "health_score" in analysis:
            score = analysis["health_score"]
            context_parts.append(f"\n=== CELKOVÉ ZDRAVOTNÉ SKÓRE ===")
            context_parts.append(f"  {score}/100")
    
    return "\n".join(context_parts) if context_parts else "Žiadne zdravotné dáta nie sú momentálne dostupné."
