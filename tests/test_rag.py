"""Retrieval over the text of uploaded reports.

The transcription of every uploaded document used to be discarded once the
numbers had been parsed out of it, so the conclusion, the medication and the
recommendation were unanswerable. These cover what replaced that: chunking,
tokenization that survives Slovak inflection, and BM25 ranking.
"""
import pytest

from app.rag import retriever

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


def _indexed(filename, document_date, text, position=0):
    chunk = retriever._IndexedChunk(
        document_id=1, filename=filename, document_date=document_date,
        chunk_index=position, text=text,
    )
    chunk.tokens = retriever._tokenize(text)
    return chunk


@pytest.fixture
def corpus(monkeypatch):
    """Index the fixtures instead of reading the database."""
    def _load(chunks=None):
        return [_indexed(name, date, text, i)
                for i, (name, date, text) in enumerate(FIXTURES)]

    monkeypatch.setattr(retriever, "_load_chunks", _load)
    retriever.invalidate_cache()
    yield
    retriever.invalidate_cache()


@pytest.mark.parametrize("a,b", [
    # Slovak is inflected: a question and a report rarely use the same case
    ("cholesterol", "cholesterolu"),
    ("cholesterol", "cholesterolom"),
    ("hypertenzia", "hypertenzie"),
    # diacritics are folded, so a query typed without them still lands
    ("Ľavá komora", "lava komora"),
])
def test_tokens_collapse_to_the_same_key(a, b):
    assert retriever._tokenize(a) == retriever._tokenize(b)


@pytest.mark.parametrize("text", ["a v na to je", "", "ak by som"])
def test_tokens_with_no_retrieval_signal_are_dropped(text):
    assert retriever._tokenize(text) == []


def test_short_text_is_one_chunk():
    text = "Záver: pacient je v poriadku."
    assert retriever.chunk_text(text) == [text]


def test_no_text_is_no_chunks():
    assert retriever.chunk_text("") == []


def test_a_long_unbroken_block_is_split_rather_than_skipped():
    pieces = retriever.chunk_text("Hodnota " * 400)

    assert len(pieces) >= 3
    ceiling = retriever.MAX_CHUNK_CHARS + retriever.CHUNK_OVERLAP_CHARS
    assert all(len(piece) <= ceiling for piece in pieces)


def test_paragraphs_are_packed_without_losing_any():
    source = "\n\n".join(f"Odsek číslo {i}. " + "text " * 40 for i in range(6))

    joined = "\n".join(retriever.chunk_text(source))

    assert all(f"Odsek číslo {i}" in joined for i in range(6))


def test_an_inflected_question_matches_the_report(corpus):
    hits = retriever.search("aký mám cholesterol?")

    assert hits[0]["document"] == "labak-jun.pdf"
    assert hits[0]["date"] == "2026-06-02"
    assert "cholesterol" in hits[0]["text"].lower()


def test_free_text_question_structured_metrics_cannot_answer(corpus):
    hits = retriever.search("čo písal kardiológ a aké lieky mi predpísal?")

    assert hits[0]["document"] == "kardio-marec.pdf"
    assert "Prestarium" in hits[0]["text"]


def test_query_without_diacritics_still_lands(corpus):
    assert retriever.search("ejekcna frakcia lavej komory")[0]["chunk_index"] == 1


@pytest.mark.parametrize("query", ["ahoj", "", "a to je"])
def test_a_query_with_nothing_to_match_returns_nothing(corpus, query):
    assert retriever.search(query) == []


def test_results_are_ranked_and_scored(corpus):
    hits = retriever.search("cholesterol LDL diéta", limit=4)

    assert hits == sorted(hits, key=lambda h: h["score"], reverse=True)
    assert all(hit["score"] > 0 for hit in hits)


def test_limit_is_honoured(corpus):
    assert len(retriever.search("kontrola", limit=1)) == 1


def test_a_long_passage_is_capped(monkeypatch):
    monkeypatch.setattr(retriever, "_load_chunks",
                        lambda: [_indexed("velky.pdf", None, "kontrola " * 300)])
    retriever.invalidate_cache()

    hit = retriever.search("kontrola")[0]

    assert len(hit["text"]) <= retriever.MAX_PASSAGE_CHARS + 10
    assert hit["text"].endswith("[…]")


def test_an_empty_corpus_is_a_normal_state(monkeypatch):
    monkeypatch.setattr(retriever, "_load_chunks", list)
    retriever.invalidate_cache()

    assert retriever.search("cholesterol") == []
