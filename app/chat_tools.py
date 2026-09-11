"""Tools the chat assistant can call to fetch data it was not handed.

The context built by app.analysis.chat_context is a snapshot: latest values,
trends, a recent window of daily aggregates, the family history, the documents
on file and the passages matching the question. That covers most questions and
costs the same tokens every time — which is exactly why it cannot also carry
years of per-day history for every metric.

These tools are how the assistant reaches past the snapshot. "Ako mi šiel LDL
za posledné dva roky" needs a series the context does not hold; "čo presne
stálo v tej marcovej správe" needs more of a document than one retrieved
passage. Without tools the model has two bad options — answer from the latest
value and a trend label, or say it cannot tell. With them it fetches the series
and answers from it.

Kept deliberately small. Family history, risk scores and the document list are
already in every prompt, so a tool for them would only add a round trip.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.analysis.chat_context import metric_history
from app.rag import search as search_documents

logger = logging.getLogger(__name__)

# Each tool round is another API round trip, so the loop is short on purpose:
# enough for "look something up, then answer", not enough to wander.
MAX_TOOL_ROUNDS = 3

# A document can be long; the assistant gets a bounded slice, and can search
# for a different part if it needs one.
MAX_DOCUMENT_CHARS = 4000

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "get_metric_history",
        "description": (
            "Vráti agregovanú históriu jednej metriky za ľubovoľné obdobie. "
            "Použi vždy, keď sa otázka týka obdobia mimo posledných dní uvedených "
            "v kontexte (napr. 'za posledný rok', 'od januára', 'za dva roky'), "
            "alebo keď potrebuješ vývoj hodnoty v čase. Názvy metrík sú v sekcii "
            "NAJNOVŠIA HODNOTA KAŽDEJ METRIKY."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "description": "Názov metriky, napr. 'weight', 'heart_rate', 'glucose'.",
                },
                "start_date": {"type": "string", "description": "YYYY-MM-DD, voliteľné."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD, voliteľné."},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "search_documents",
        "description": (
            "Vyhľadá úryvky v nahraných lekárskych správach. Použi, keď sa otázka "
            "týka obsahu správy (záver lekára, lieky, odporúčanie) a úryvky už "
            "vložené do kontextu nestačia alebo sa týkajú niečoho iného."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Čo hľadať, slovensky."},
                "limit": {"type": "integer", "description": "Počet úryvkov, max 8."},
            },
            "required": ["query"],
        },
    },
]


def _get_metric_history(payload: Dict[str, Any]) -> Dict[str, Any]:
    return metric_history(
        metric=payload.get("metric", ""),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
    )


def _search_documents(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        limit = int(payload.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    results = search_documents(payload.get("query", ""), limit=max(1, min(limit, 8)))
    for result in results:
        text = result.get("text") or ""
        if len(text) > MAX_DOCUMENT_CHARS:
            result["text"] = text[:MAX_DOCUMENT_CHARS] + " […]"
    return {"results": results, "count": len(results)}


_HANDLERS = {
    "get_metric_history": _get_metric_history,
    "search_documents": _search_documents,
}


def run_tool(name: str, payload: Dict[str, Any]) -> str:
    """Execute one tool call and return its result as JSON text.

    A failing tool comes back as an error string rather than an exception: the
    model can say what it could not look up, which is a better answer than a
    500 for the whole question.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Neznámy nástroj: {name}"}, ensure_ascii=False)

    try:
        result = handler(payload or {})
    except Exception as e:
        logger.warning("chat tool %s failed: %s", name, e)
        result = {"error": f"Nástroj zlyhal: {e}"}

    return json.dumps(result, ensure_ascii=False, default=str)
