"""Retrieval tests: chunking, Slovak-tolerant tokenization and BM25 ranking.

app.database is stubbed because sqlalchemy is not installable in this sandbox;
_load_chunks is replaced so the index is built from fixtures instead of a DB.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

db = types.ModuleType("app.database")
db.Document = type("Document", (), {})
db.DocumentChunk = type("DocumentChunk", (), {})
db.get_session = lambda: None
sys.modules["app.database"] = db

from app.rag import retriever  # noqa: E402

# --- tokenization -----------------------------------------------------------
# Slovak inflection: all three forms must collapse to one key, otherwise a
# question about "cholesterol" never matches a report saying "cholesterolu".
assert retriever._tokenize("cholesterol") == retriever._tokenize("cholesterolu")
assert retriever._tokenize("cholesterol") == retriever._tokenize("cholesterolom")
# diacritics folded
assert retriever._tokenize("Ľavá komora") == retriever._tokenize("lava komora")
# stopwords and short tokens dropped
assert retriever._tokenize("a v na to je") == []
assert "daj" not in " ".join(retriever._tokenize("daj mi výsledky"))

# --- chunking ---------------------------------------------------------------
short = "Záver: pacient je v poriadku."
assert retriever.chunk_text(short) == [short]
assert retriever.chunk_text("") == []

long_paragraph = "Hodnota " * 400  # ~3200 chars, no paragraph breaks
pieces = retriever.chunk_text(long_paragraph)
assert len(pieces) >= 3, len(pieces)
assert all(len(p) <= retriever.MAX_CHUNK_CHARS + retriever.CHUNK_OVERLAP_CHARS for p in pieces)

multi = "\n\n".join([f"Odsek číslo {i}. " + "text " * 40 for i in range(6)])
pieces = retriever.chunk_text(multi)
assert len(pieces) >= 2
assert "Odsek číslo 0" in pieces[0]
# nothing is silently dropped
assert all(f"Odsek číslo {i}" in "\n".join(pieces) for i in range(6))

# --- retrieval --------------------------------------------------------------
FIXTURES = [
    ("kardio-marec.pdf", "2026-03-14",
     "Záver kardiológa: ľahká hypertenzia. Odporúčam Prestarium 5 mg denne "
     "a kontrolu krvného tlaku o tri mesiace."),
    ("kardio-marec.pdf", "2026-03-14",
     "Echokardiografia: ejekčná frakcia ľavej komory 58 %, chlopne bez patológie."),
    ("labak-jun.pdf", "2026-06-02",
     "Lipidový profil: celkový cholesterol 6,4 mmol/l, LDL 4,1 mmol/l. "
     "Odporúčaná diétna úprava a kontrola o pol roka."),
    ("labak-jun.pdf", "2026-06-02",
     "Glykémia nalačno 5,2 mmol/l, v norme. Pečeňové testy bez odchýlok."),
]


def _fake_chunks():
    out = []
    for i, (filename, date, text) in enumerate(FIXTURES):
        chunk = retriever._IndexedChunk(
            document_id=1, filename=filename, document_date=date,
            chunk_index=i, text=text,
        )
        chunk.tokens = retriever._tokenize(text)
        out.append(chunk)
    return out


retriever._load_chunks = _fake_chunks
retriever.invalidate_cache()

# asked in a different grammatical case than the document uses
hits = retriever.search("aký mám cholesterol?")
assert hits, "inflected query must still match"
assert hits[0]["document"] == "labak-jun.pdf", hits[0]
assert "cholesterol" in hits[0]["text"].lower()
assert hits[0]["date"] == "2026-06-02"

# the free-text question that structured metrics can never answer
hits = retriever.search("čo písal kardiológ a aké lieky mi predpísal?")
assert hits[0]["document"] == "kardio-marec.pdf", hits[0]
assert "Prestarium" in hits[0]["text"]

# diacritics-free query still lands
assert retriever.search("ejekcna frakcia lavej komory")[0]["chunk_index"] == 1

# a question with nothing to match returns nothing rather than noise
assert retriever.search("ahoj") == []
assert retriever.search("") == []

# ranking is a ranking, not an arbitrary order
scored = retriever.search("cholesterol LDL diéta", limit=4)
assert scored[0]["score"] >= scored[-1]["score"]
assert all(h["score"] > 0 for h in scored)

# limit is honoured and passages are capped
assert len(retriever.search("kontrola", limit=1)) <= 1
long_chunk = retriever._IndexedChunk(1, "velky.pdf", None, 0, "kontrola " * 300)
long_chunk.tokens = retriever._tokenize(long_chunk.text)
retriever._load_chunks = lambda: [long_chunk]
retriever.invalidate_cache()
hit = retriever.search("kontrola")[0]
assert len(hit["text"]) <= retriever.MAX_PASSAGE_CHARS + 10, len(hit["text"])

# empty corpus is a normal state
retriever._load_chunks = lambda: []
retriever.invalidate_cache()
assert retriever.search("cholesterol") == []

print("RAG: ALL ASSERTIONS PASSED")
retriever._load_chunks = _fake_chunks
retriever.invalidate_cache()
for h in retriever.search("čo písal kardiológ a aké lieky mi predpísal?", limit=2):
    print(f"  {h['score']:>6}  [{h['document']}, {h['date']}]  {h['text'][:80]}…")
