"""A scanned health card is bigger than one request, and it is not just numbers.

Uploading a 33 MB, ~100-page card failed with 413 request_too_large: the whole
PDF went into a single Messages API call, which caps a request at 32 MB — and
base64 adds a third on top — and at 100 pages. Splitting into page batches
fixes both.

The second half of the problem was quieter: the extraction prompt asked only
for a JSON array of lab values, so the operations, diagnoses and doctors'
conclusions on those pages were never produced at all, and "aké som mal
operácie" had no source to answer from however many times the card was
uploaded. The reply now carries a written record alongside the metrics, and
these cover keeping the two apart.
"""
import io
import json

import pytest

from app.ocr import document_processor as dp


def _pdf(pages: int, page_size=(612, 792)) -> bytes:
    writer = dp.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=page_size[0], height=page_size[1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def pdf_file(tmp_path):
    def _make(pages):
        path = tmp_path / f"scan_{pages}.pdf"
        path.write_bytes(_pdf(pages))
        return path
    return _make


# --- splitting the reply ----------------------------------------------------

def test_record_and_metrics_are_separated():
    reply = (
        f"{dp.RECORD_MARKER}\n"
        "Operácia: apendektómia, 12. 3. 2019, NsP Trenčín.\n"
        "Referenčné rozpätie [3.5-5.5] je uvedené v správe.\n"
        f"{dp.METRICS_MARKER}\n"
        '[{"metric": "glucose", "value": 5.2}]'
    )

    record, metrics = dp.split_document_output(reply)

    assert "apendektómia" in record
    assert dp.RECORD_MARKER not in record
    # the bracket in the record must not reach the extractor, which reads from
    # the first '[' to the last ']'
    assert "[3.5-5.5]" not in metrics
    assert json.loads(metrics)[0]["metric"] == "glucose"


def test_a_reply_without_the_marker_is_treated_as_metrics_only():
    """What an older document, or a model ignoring the format, produces."""
    reply = '[{"metric": "ldl", "value": 3.6}]'

    record, metrics = dp.split_document_output(reply)

    assert record == ""
    assert metrics == reply


@pytest.mark.parametrize("dash", ["-", "—", "–"])
def test_a_page_of_nothing_but_a_lab_table_records_nothing(dash):
    record, _ = dp.split_document_output(
        f"{dp.RECORD_MARKER}\n{dash}\n{dp.METRICS_MARKER}\n[]")

    assert record == ""


# --- merging batches --------------------------------------------------------

def test_batches_merge_into_one_record_and_one_array():
    outputs = [
        f"{dp.RECORD_MARKER}\nOperácia: apendektómia 2019.\n{dp.METRICS_MARKER}\n"
        '[{"metric": "glucose", "value": 5.2}]',
        f"{dp.RECORD_MARKER}\nZáver: hypertenzia, Prestarium.\n{dp.METRICS_MARKER}\n"
        '[{"metric": "ldl", "value": 3.6}]',
    ]

    record, metrics = dp.split_document_output(dp._merge_outputs(outputs))

    assert "apendektómia" in record and "Prestarium" in record
    parsed = json.loads(metrics)
    assert [m["metric"] for m in parsed] == ["glucose", "ldl"]


def test_one_malformed_batch_does_not_cost_the_others_their_metrics():
    outputs = [
        f"{dp.RECORD_MARKER}\nStrana 1.\n{dp.METRICS_MARKER}\n"
        '[{"metric": "glucose", "value": 5.2}]',
        f"{dp.RECORD_MARKER}\nStrana 2.\n{dp.METRICS_MARKER}\nnie som JSON",
    ]

    record, metrics = dp.split_document_output(dp._merge_outputs(outputs))

    assert json.loads(metrics) == [{"metric": "glucose", "value": 5.2}]
    # the unparsed reply is kept rather than dropped silently
    assert "nie som JSON" in record


# --- splitting the PDF ------------------------------------------------------

def test_a_small_pdf_is_sent_whole(pdf_file):
    batches = dp._pdf_batches(pdf_file(3))

    assert len(batches) == 1


def test_a_long_pdf_is_split_and_keeps_every_page(pdf_file):
    path = pdf_file(100)

    batches = dp._pdf_batches(path)

    assert len(batches) == 10, "100 pages at 10 per batch"
    total = sum(len(dp.PdfReader(io.BytesIO(b)).pages) for b in batches)
    assert total == 100, "no page may be lost in the split"
    assert all(len(dp.PdfReader(io.BytesIO(b)).pages) <= dp.MAX_PAGES_PER_BATCH
               for b in batches)


def test_every_batch_stays_well_under_the_request_limit(pdf_file):
    for batch in dp._pdf_batches(pdf_file(45)):
        # base64 adds a third; the API refuses a request over 32 MB
        assert len(batch) * 4 / 3 < 32 * 1024 * 1024


def test_an_empty_pdf_is_an_error_not_an_empty_result(pdf_file):
    with pytest.raises(ValueError):
        dp._pdf_batches(pdf_file(0))


# --- the call itself --------------------------------------------------------

class _FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.replies.pop(0) if self.replies else "[]"
        return type("R", (), {"content": [type("B", (), {"text": text})()]})()


def _processor(replies):
    processor = dp.DocumentProcessor.__new__(dp.DocumentProcessor)
    processor.client = type("C", (), {"messages": _FakeMessages(replies)})()
    return processor


def test_a_long_pdf_is_one_request_per_batch(pdf_file):
    processor = _processor([
        f"{dp.RECORD_MARKER}\nStrana {i}.\n{dp.METRICS_MARKER}\n[]" for i in range(10)
    ])

    text = processor.process_document(pdf_file(100))

    assert len(processor.client.messages.calls) == 10
    record, metrics = dp.split_document_output(text)
    assert "Strana 0." in record and "Strana 9." in record
    assert json.loads(metrics) == []


def test_a_short_pdf_is_a_single_request_returned_unchanged(pdf_file):
    reply = f"{dp.RECORD_MARKER}\nJedna strana.\n{dp.METRICS_MARKER}\n[]"
    processor = _processor([reply])

    assert processor.process_document(pdf_file(2)) == reply
    assert len(processor.client.messages.calls) == 1
