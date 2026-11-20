"""Consensus (consensus.app): recenzované štúdie pre odpovede v chate.

Na otázky typu „pomáha kreatín na pamäť?“ model doteraz odpovedal z vlastnej
pamäti („niektoré štúdie naznačujú…“) bez jedinej citácie. Tento nástroj mu
dá konkrétne štúdie s rokom, časopisom a odkazom.

Do Consensus ide IBA všeobecná vedecká otázka, ktorú model sformuluje
anglicky. Žiadne údaje pacienta (hodnoty, vek, diagnózy) sa neposielajú —
schéma nástroja to modelu výslovne zakazuje a otázka sa tu ešte skracuje.

API: GET https://api.consensus.app/v1/search, hlavička x-api-key.
Free plán má 30 volaní mesačne, preto sa výsledky cachujú.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.consensus.app/v1/search"
TIMEOUT_S = 8.0
MAX_RESULTS = 5
MAX_QUERY_CHARS = 300
CACHE_TTL_S = 7 * 24 * 3600

STUDY_TYPES = (
    "rct", "meta-analysis", "systematic review", "cohort",
    "case-control", "observational", "case report",
)

_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

SEARCH_STUDIES_SCHEMA: Dict[str, Any] = {
    "name": "search_studies",
    "description": (
        "Vyhľadá recenzované vedecké štúdie (Consensus, 200M+ článkov). Použi VŽDY, keď sa "
        "pacient pýta, či niečo (doplnok, liek, strava, cvičenie, návyk, terapia) pomáha, "
        "funguje, je účinné alebo bezpečné, alebo aké sú o tom dôkazy. Query napíš ANGLICKY, "
        "krátko a VŠEOBECNE, napr. 'creatine supplementation memory'. NIKDY do nej nevkladaj "
        "údaje pacienta (vek, hodnoty, diagnózy, lieky)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Všeobecná vedecká otázka v angličtine, bez osobných údajov.",
            },
            "study_types": {
                "type": "array",
                "items": {"type": "string", "enum": list(STUDY_TYPES)},
                "description": "Voliteľný filter typu štúdií, napr. ['meta-analysis', 'rct'].",
            },
            "year_min": {"type": "integer", "description": "Voliteľne najstarší rok publikácie."},
        },
        "required": ["query"],
    },
}


def is_enabled() -> bool:
    return bool((settings.CONSENSUS_API_KEY or "").strip())


def _paper(raw: Dict[str, Any]) -> Dict[str, Any]:
    doi = raw.get("doi")
    return {
        "title": raw.get("title"),
        "year": raw.get("publish_year"),
        "journal": raw.get("journal_name"),
        "citations": raw.get("citation_count"),
        "takeaway": raw.get("takeaway"),
        "url": raw.get("url") or (f"https://doi.org/{doi}" if doi else None),
    }


def search_studies(
    query: str,
    study_types: Optional[List[str]] = None,
    year_min: Optional[int] = None,
) -> Dict[str, Any]:
    """Štúdie k otázke. Chyba sa vracia ako {"error": ...}, nikdy nevyhodí výnimku,
    aby chat vedel odpovedať aj bez nich."""
    key = (settings.CONSENSUS_API_KEY or "").strip()
    if not key:
        return {"error": "Vyhľadávanie štúdií nie je nakonfigurované."}

    query = (query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return {"error": "Prázdna otázka."}

    params: Dict[str, Any] = {"query": query, "page_size": MAX_RESULTS}
    types = [t for t in (study_types or []) if t in STUDY_TYPES]
    if types:
        params["study_types"] = ",".join(types)
    if year_min:
        try:
            params["year_min"] = int(year_min)
        except (TypeError, ValueError):
            pass

    cache_key = json.dumps(params, sort_keys=True)
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]

    try:
        response = httpx.get(API_URL, params=params, headers={"x-api-key": key}, timeout=TIMEOUT_S)
        response.raise_for_status()
        raw = (response.json() or {}).get("results") or []
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        logger.warning("[CONSENSUS] HTTP %s", status)
        if status == 429:
            return {"error": "Mesačný limit vyhľadávania štúdií je vyčerpaný."}
        return {"error": "Vyhľadávanie štúdií je dočasne nedostupné."}
    except Exception as e:
        logger.warning("[CONSENSUS] search failed: %s", e)
        return {"error": "Vyhľadávanie štúdií je dočasne nedostupné."}

    result = {
        "source": "Consensus (consensus.app)",
        "query": query,
        "studies": [_paper(p) for p in raw[:MAX_RESULTS]],
    }
    logger.info("[CONSENSUS] %r -> %d studies", query, len(result["studies"]))
    _cache[cache_key] = (time.time(), result)
    return result
