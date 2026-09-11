"""Retrieval over the text of uploaded medical documents.

The numbers a report contains are already parsed into health_records, and the
chat answers questions about them from SQL aggregates — that part must stay
deterministic, because "priemer za posledné 3 dni" is arithmetic, not
similarity. What was lost was everything around the numbers: the doctor's
conclusion, the medication, the recommendation, the reason for the referral.
process_document transcribed all of it and the text was thrown away as soon as
the metrics had been extracted. It is now stored and indexed here, so the
assistant can answer "čo písal kardiológ v marci".

Why lexical BM25 and not embeddings: the corpus is a few dozen reports, the
Railway Postgres runs the stock postgres:17 image with no pgvector, and the
SQLite fallback has no vector support either. BM25 over a few hundred chunks
scores in milliseconds, needs no extension, no new dependency and no embedding
API key, and it is inspectable — when a passage comes back wrong you can see
which term matched. The seam for embeddings is `_score`: give a chunk a vector,
score it by cosine, blend the two rankings. That upgrade is worth making when
the corpus stops fitting in one pass, not before.

Slovak is heavily inflected and Postgres ships no Slovak dictionary, so tokens
are folded to ASCII and truncated to a stem length. "cholesterolu",
"cholesterol" and "cholesterolom" collapse to the same key — crude next to a
real stemmer, and far better than exact matching for this language.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from app.database import Document, DocumentChunk, get_session

logger = logging.getLogger(__name__)

# Chunking. Medical reports are short and densely packed, so chunks stay small
# enough that a retrieved passage is mostly signal, with an overlap so a value
# and the sentence interpreting it do not end up split apart.
MAX_CHUNK_CHARS = 900
CHUNK_OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 40

# Retrieval
DEFAULT_TOP_K = 5
MAX_PASSAGE_CHARS = 700
STEM_LENGTH = 6
MIN_TOKEN_LENGTH = 3

# BM25 constants, standard values.
_K1 = 1.5
_B = 0.75

_CACHE_TTL_SECONDS = 300

# Words that carry no retrieval signal in a Slovak health question. Folded to
# ASCII and stemmed like every other token, so they are compared on equal terms.
_STOPWORDS_RAW = (
    "a aby aj ak ako ale alebo ani area az bez bol bola boli bolo bud by "
    "cez co ci cim dalsie do ho cha i ich iba je jeho jej ju k kam kde ked "
    "kto ktora ktore ktory lebo len ma mam me mi mna mne mnou moj moja moje "
    "mozem na nad nam nas nie nich nim no o od pod pre preco pri prosim "
    "s sa si so su ta tak takze tam te teda ten tento to toto tu ty u v vo "
    "vsak vsetko z za ze zo "
    # forms of byť/mať and the filler words a spoken question carries; they
    # appear in nearly every chunk, so they rank nothing and only add noise
    "som sme ste bol bola boli bude budem budes budu mal mala mali mam mas "
    "este uz tiez potom preto velmi asi len iba "
    "daj urob povedz ake aky aka kolko"
)


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so 'Ľavá' and 'lava' match."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem(token: str) -> str:
    return token[:STEM_LENGTH]


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    tokens = []
    for raw in _TOKEN_RE.findall(_fold(text)):
        if len(raw) < MIN_TOKEN_LENGTH:
            continue
        stem = _stem(raw)
        if stem in _STOPWORD_STEMS:
            continue
        tokens.append(stem)
    return tokens


_STOPWORD_STEMS = {
    _stem(word) for word in _fold(_STOPWORDS_RAW).split() if len(word) >= MIN_TOKEN_LENGTH
}


def chunk_text(text: str) -> List[str]:
    """Split a transcribed document into overlapping, paragraph-aligned chunks."""
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: List[str] = []
    current = ""

    def flush(block: str) -> None:
        if block.strip():
            chunks.append(block.strip())

    for paragraph in paragraphs:
        # A paragraph longer than the budget is hard-split; report tables arrive
        # as one long block often enough that skipping this loses whole pages.
        while len(paragraph) > MAX_CHUNK_CHARS:
            flush(current)
            current = ""
            cut = paragraph.rfind("\n", 0, MAX_CHUNK_CHARS)
            if cut < MIN_CHUNK_CHARS:
                cut = MAX_CHUNK_CHARS
            flush(paragraph[:cut])
            paragraph = paragraph[max(0, cut - CHUNK_OVERLAP_CHARS):]

        if not current:
            current = paragraph
        elif len(current) + len(paragraph) + 2 <= MAX_CHUNK_CHARS:
            current = f"{current}\n\n{paragraph}"
        else:
            flush(current)
            tail = current[-CHUNK_OVERLAP_CHARS:] if len(current) > CHUNK_OVERLAP_CHARS else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph

    flush(current)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or ([text.strip()] if text.strip() else [])


@dataclass
class _IndexedChunk:
    document_id: Optional[int]
    filename: str
    document_date: Optional[str]
    chunk_index: int
    text: str
    tokens: List[str] = field(default_factory=list)


class _Index:
    """BM25 over every stored chunk, held in memory between requests."""

    def __init__(self, chunks: Sequence[_IndexedChunk]):
        self.chunks = list(chunks)
        self.doc_frequency: Dict[str, int] = {}
        self.term_frequency: List[Dict[str, int]] = []
        lengths = []

        for chunk in self.chunks:
            counts: Dict[str, int] = {}
            for token in chunk.tokens:
                counts[token] = counts.get(token, 0) + 1
            self.term_frequency.append(counts)
            lengths.append(len(chunk.tokens))
            for token in counts:
                self.doc_frequency[token] = self.doc_frequency.get(token, 0) + 1

        self.total = len(self.chunks)
        self.average_length = (sum(lengths) / len(lengths)) if lengths else 0.0

    def score(self, query_tokens: Sequence[str]) -> List[float]:
        scores = [0.0] * self.total
        if not self.total or not self.average_length:
            return scores

        for token in set(query_tokens):
            df = self.doc_frequency.get(token)
            if not df:
                continue
            idf = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            for i, counts in enumerate(self.term_frequency):
                tf = counts.get(token)
                if not tf:
                    continue
                length = len(self.chunks[i].tokens) or 1
                denominator = tf + _K1 * (1 - _B + _B * length / self.average_length)
                scores[i] += idf * (tf * (_K1 + 1)) / denominator
        return scores


_index: Optional[_Index] = None
_index_built_at: Optional[datetime] = None


def invalidate_cache() -> None:
    """Drop the in-memory index so the next search reloads from the database.

    Called after an upload, for the same reason TrendAnalyzer.invalidate_cache
    is: without it a freshly uploaded report stays invisible for the TTL.
    """
    global _index, _index_built_at
    _index = None
    _index_built_at = None


def _load_chunks() -> List[_IndexedChunk]:
    session = get_session()
    try:
        rows = (
            session.query(DocumentChunk, Document)
            .outerjoin(Document, DocumentChunk.document_id == Document.id)
            .all()
        )
        chunks = []
        for chunk, document in rows:
            if not chunk.text:
                continue
            indexed = _IndexedChunk(
                document_id=chunk.document_id,
                filename=(document.filename if document else None) or "neznámy dokument",
                document_date=(
                    document.document_date.isoformat()
                    if document is not None and document.document_date
                    else None
                ),
                chunk_index=chunk.chunk_index or 0,
                text=chunk.text,
            )
            indexed.tokens = _tokenize(indexed.text)
            chunks.append(indexed)
        logger.info("RAG: indexed %d chunks from documents", len(chunks))
        return chunks
    except Exception as e:
        logger.warning("RAG: cannot load document chunks: %s", e)
        return []
    finally:
        session.close()


def _get_index() -> _Index:
    global _index, _index_built_at
    fresh = (
        _index is not None
        and _index_built_at is not None
        and (datetime.now() - _index_built_at).total_seconds() < _CACHE_TTL_SECONDS
    )
    if not fresh:
        _index = _Index(_load_chunks())
        _index_built_at = datetime.now()
    return _index


def search(query: str, limit: int = DEFAULT_TOP_K) -> List[Dict]:
    """Passages most relevant to the question, best first."""
    tokens = _tokenize(query or "")
    if not tokens:
        return []

    index = _get_index()
    if not index.total:
        return []

    scored = sorted(
        ((score, i) for i, score in enumerate(index.score(tokens)) if score > 0),
        reverse=True,
    )[:limit]

    results = []
    for score, i in scored:
        chunk = index.chunks[i]
        text = chunk.text
        if len(text) > MAX_PASSAGE_CHARS:
            text = text[:MAX_PASSAGE_CHARS].rsplit(" ", 1)[0] + " […]"
        results.append({
            "document": chunk.filename,
            "date": chunk.document_date,
            "chunk_index": chunk.chunk_index,
            "score": round(score, 3),
            "text": text,
        })
    return results


def document_inventory() -> List[Dict]:
    """Every stored document, so the assistant knows what exists at all.

    Retrieval can miss; a list of what is on file cannot. It is small enough to
    carry in every prompt and stops the model from claiming a report is not
    there when it simply did not match the query terms.
    """
    session = get_session()
    try:
        documents = session.query(Document).order_by(Document.uploaded_at.desc()).all()
        inventory = []
        for document in documents:
            inventory.append({
                "filename": document.filename,
                "type": document.document_type,
                "date": document.document_date.isoformat() if document.document_date else None,
                "uploaded_at": document.uploaded_at.isoformat() if document.uploaded_at else None,
                "has_text": bool(document.ocr_text),
            })
        return inventory
    except Exception as e:
        logger.warning("RAG: cannot list documents: %s", e)
        return []
    finally:
        session.close()


def index_document(
    filename: str,
    text: str,
    file_path: Optional[str] = None,
    file_type: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
    document_date=None,
    document_type: Optional[str] = None,
) -> Optional[int]:
    """Store a transcribed document and its chunks. Returns the document id.

    Re-uploading the same filename replaces the previous text and chunks rather
    than stacking a second copy, so retrieval cannot return the same passage
    twice with different wording.
    """
    if not text or not text.strip():
        return None

    session = get_session()
    try:
        document = session.query(Document).filter_by(filename=filename).first()
        if document is None:
            document = Document(filename=filename)
            session.add(document)

        document.file_path = file_path or document.file_path or ""
        document.file_type = file_type or document.file_type
        document.file_size_bytes = file_size_bytes or document.file_size_bytes
        document.ocr_text = text
        document.ocr_processed = True
        document.processing_status = "completed"
        document.processed_at = datetime.now()
        if document_date is not None:
            document.document_date = document_date
        if document_type is not None:
            document.document_type = document_type

        session.flush()  # assigns document.id for a new row

        session.query(DocumentChunk).filter_by(document_id=document.id).delete()
        for position, piece in enumerate(chunk_text(text)):
            session.add(DocumentChunk(
                document_id=document.id,
                chunk_index=position,
                text=piece,
            ))

        session.commit()
        document_id = document.id
        logger.info("RAG: stored document %s (id=%s)", filename, document_id)
        invalidate_cache()
        return document_id

    except Exception as e:
        session.rollback()
        logger.warning("RAG: cannot store document %s: %s", filename, e)
        return None
    finally:
        session.close()
