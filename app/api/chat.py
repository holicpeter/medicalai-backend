import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import anthropic
from app.analysis.chat_context import build_health_context, format_health_context
from app.chat_tools import MAX_TOOL_ROUNDS, TOOL_SCHEMAS, run_tool
from app.config import settings
from app.database import ChatMessage, Patient, get_session

_MODEL = "claude-haiku-4-5-20251001"

# Turns replayed into the prompt. Enough for "a čo tie lieky?" to know what was
# just discussed, short enough that an old conversation is not paid for on
# every question.
MAX_HISTORY_TURNS = 6

# A stored answer can run to a couple of thousand characters. The full text is
# kept for the history endpoint; the replay is trimmed, because what a
# follow-up needs is the subject, not the whole report.
MAX_REPLAYED_CHARS = 800

logger = logging.getLogger(__name__)

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
        # The context is built here, from the database, rather than taken from
        # the request body. The client used to be the only source: it loaded a
        # snapshot and posted it back, so whenever that load failed — and it
        # always did, because the endpoint it calls did not exist — every
        # question reached the model with no data attached and got "nemám
        # žiadne údaje" as the honest answer to an empty context.
        context = format_health_context(build_health_context(question=request.question))

        if not context:
            # Nothing stored yet. A client-supplied snapshot is still accepted
            # so an older frontend keeps working against a new backend.
            context = _prepare_health_context(request.health_data)

        # Vytvoríme prompt pre Claude AI
        system_prompt = """Si odborný zdravotný asistent s hlbokými znalosťami medicíny.
Tvoja úloha je odpovedať na otázky pacienta o jeho zdravotných výsledkoch.

DÔLEŽITÉ PRAVIDLÁ:
- Odpovedaj VÝHRADNE v slovenskom jazyku
- Buď presný, faktický a opieraj sa len o poskytnuté dáta
- Nikdy si nevymýšľaj hodnoty, ktoré v dátach nie sú
- Ak sa pacient pýta na obdobie, za ktoré nie sú merania, NEPÍŠ, že žiadne dáta
  neexistujú. Povedz, že za dané obdobie nie sú merania, a odpovedz na základe
  najnovších dostupných hodnôt — vždy uveď, z ktorého dátumu pochádzajú
- Pri hodnotách uvádzaj dátum merania a zdroj, ak je relevantný
- Ak čerpáš z úryvku lekárskej správy, uveď názov dokumentu a jeho dátum
- Merania nie sú to isté čo obsah zdravotnej karty. Nikdy netvrď, že vidíš
  naskenované správy alebo celú kartu, ak máš k dispozícii len namerané hodnoty —
  v sekcii NAHRANÉ LEKÁRSKE DOKUMENTY je napísané, čo z dokumentov naozaj máš
- Úryvky sú vyhľadané k otázke; ak medzi nimi odpoveď nie je, povedz to a
  neodvodzuj obsah správy, ktorý nemáš — v zozname vyššie je, aké dokumenty
  vôbec existujú
- Nikdy nediagnostikuj choroby - len informuj o hodnotách a trendoch
- Odporúčaj konzultáciu s lekárom pri akýchkoľvek abnormálnych hodnotách
- Buď empatický a zrozumiteľný
- Vysvetľuj medicínske pojmy jednoducho

NÁSTROJE:
Kontext obsahuje len posledné obdobie. Ak sa otázka týka dlhšieho obdobia alebo
vývoja hodnoty v čase, zavolaj get_metric_history namiesto odhadovania z
poslednej hodnoty. Ak potrebuješ iný obsah lekárskej správy než sú priložené
úryvky, zavolaj search_documents. Neospravedlňuj sa za volanie nástroja a
nespomínaj ho v odpovedi — pacienta zaujíma výsledok."""

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
            history = await asyncio.to_thread(load_history)
            # Off the event loop: the SDK call is blocking and there can now be
            # several of them in one question.
            answer = await asyncio.to_thread(
                _ask_claude, client, system_prompt, user_prompt, history
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Chýba API kľúč pre Mistral alebo Claude. Pridaj MISTRAL_API_KEY alebo ANTHROPIC_API_KEY do .env",
            )

        await asyncio.to_thread(_save_turn, request.question, answer)
        return ChatResponse(answer=answer)

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Chat error: %s', e)
        raise HTTPException(status_code=500, detail=f"Chyba pri spracovaní otázky: {str(e)}")


def load_history(limit: int = MAX_HISTORY_TURNS, full: bool = False) -> list:
    """The last turns, oldest first, in the shape the Messages API expects.

    A failure here must not cost the patient an answer: a chat without memory
    is worse than one with it, and far better than a 500.
    """
    session = None
    try:
        # Acquired inside the try: if the database is unreachable, get_session
        # itself raises, and a chat without memory beats a chat that 500s.
        session = get_session()
        rows = (
            session.query(ChatMessage)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
        history = []
        for row in rows:
            content = row.content or ''
            if not full and len(content) > MAX_REPLAYED_CHARS:
                content = content[:MAX_REPLAYED_CHARS] + ' […]'
            entry = {"role": row.role or 'user', "content": content}
            if full:
                entry["created_at"] = row.created_at.isoformat() if row.created_at else None
            history.append(entry)
        return history
    except Exception as e:
        logger.warning('chat history: cannot load: %s', e)
        return []
    finally:
        if session is not None:
            session.close()


def _save_turn(question: str, answer: str) -> None:
    session = None
    try:
        session = get_session()
        patient = session.query(Patient).first()
        patient_id = patient.id if patient else None
        session.add(ChatMessage(patient_id=patient_id, role='user', content=question))
        session.add(ChatMessage(patient_id=patient_id, role='assistant', content=answer))
        session.commit()
    except Exception as e:
        if session is not None:
            session.rollback()
        logger.warning('chat history: cannot save turn: %s', e)
    finally:
        if session is not None:
            session.close()


@router.get("/history")
async def get_chat_history(limit: int = 50):
    """Every question and answer, with its timestamp, oldest first."""
    return {"messages": load_history(limit=max(1, min(limit, 500)), full=True)}


def _ask_claude(client, system_prompt: str, user_prompt: str, history: Optional[list] = None) -> str:
    """Ask, letting the model fetch what the prompt does not already carry.

    The context is a snapshot of the recent window; a question about a longer
    period used to be answered from the latest value and a trend label, because
    that was all the model had. Now it can call for the series instead. The
    loop is short — a couple of lookups, then an answer.
    """
    # Earlier turns go in front of this question, so a follow-up that says
    # "a čo tie lieky?" knows what "tie" refers to. Only the questions and
    # answers are replayed — the health context is rebuilt fresh each time and
    # would otherwise be paid for once per remembered turn.
    messages = list(history or []) + [{"role": "user", "content": user_prompt}]
    response = None

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            logger.info("chat: tool %s(%s)", block.name, block.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": run_tool(block.name, block.input),
            })
        messages.append({"role": "user", "content": results})
    else:
        # Still asking for tools with the budget spent. One more turn with the
        # tools withheld, so the model has to answer from what it already
        # gathered — a tool_use turn carries no text, and returning that would
        # show the patient an empty reply.
        logger.info("chat: tool budget exhausted, answering without tools")
        response = client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )

    answer = "".join(
        block.text
        for block in (response.content if response is not None else [])
        if getattr(block, "type", None) == "text"
    ).strip()

    return answer or "Prepáčte, na túto otázku sa mi nepodarilo zostaviť odpoveď."


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
