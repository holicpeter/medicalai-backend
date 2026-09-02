"""A compressed export must import exactly like the plain XML it contains.

Apple Health XML is repetitive enough to compress about 27x, so accepting an
archive is what keeps a large export under the 100 MB request body limit that
Cloudflare enforces at the edge — a limit no paid plan lifts to 1 GB.

The size guard matters as much as the format support: without a cap on what is
written, a small archive can expand until it fills the container's disk.
"""
import gzip
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api import apple_health
from app.main import app

EXPORT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="sk_SK">
 <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count"
   startDate="2023-05-13 15:30:45 +0200" endDate="2023-05-13 15:35:45 +0200" value="512"/>
</HealthData>
"""

CDA_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3"><component/></ClinicalDocument>
"""


@pytest.fixture
def client():
    return TestClient(app)


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, payload in members.items():
            z.writestr(name, payload)
    return buf.getvalue()


def _post(client, filename, payload):
    return client.post(
        "/api/apple-health/import",
        files={"file": (filename, payload, "application/octet-stream")},
    )


def test_gzipped_export_is_imported(client):
    r = _post(client, "export.xml.gz", gzip.compress(EXPORT_XML))
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["total_records"] == 1


def test_zipped_export_is_imported(client):
    payload = _zip({"apple_health_export/export.xml": EXPORT_XML})
    r = _post(client, "export.zip", payload)
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["total_records"] == 1


def test_plain_xml_still_works(client):
    r = _post(client, "export.xml", EXPORT_XML)
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["total_records"] == 1


def test_zip_picks_measurements_over_cda(client):
    """Apple ships both files in one archive; the CDA one has no records."""
    payload = _zip({
        "apple_health_export/export_cda.xml": CDA_XML,
        "apple_health_export/export.xml": EXPORT_XML,
    })
    r = _post(client, "export.zip", payload)
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["total_records"] == 1


def test_zip_of_only_cda_is_rejected_with_guidance(client):
    payload = _zip({"apple_health_export/export_cda.xml": CDA_XML})
    r = _post(client, "export.zip", payload)
    assert r.status_code == 400
    assert "export.xml" in r.json()["detail"]


def test_gz_extension_on_uncompressed_bytes_is_a_clear_400(client):
    """A mislabelled file must not surface as a 500 from zlib."""
    r = _post(client, "export.xml.gz", EXPORT_XML)
    assert r.status_code == 400
    assert "gzip" in r.json()["detail"].lower()


def test_corrupt_zip_is_a_clear_400(client):
    r = _post(client, "export.zip", b"PK\x03\x04 not really a zip")
    assert r.status_code == 400
    assert "zip" in r.json()["detail"].lower()


def test_unsupported_extension_names_the_accepted_ones(client):
    r = _post(client, "export.tar.bz2", EXPORT_XML)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert ".xml.gz" in detail and ".zip" in detail


def test_decompression_is_capped(client, monkeypatch):
    """A gzip bomb must be refused while expanding, not after filling the disk."""
    monkeypatch.setattr(apple_health, "_MAX_DECOMPRESSED_BYTES", 4096)
    bomb = gzip.compress(b"<HealthData>" + b" " * 200_000 + b"</HealthData>")
    assert len(bomb) < 4096, "the compressed payload must pass the received cap"

    r = _post(client, "export.xml.gz", bomb)
    assert r.status_code == 413


def test_received_size_is_reported_not_expanded_size(client):
    """size_mb describes the upload, so a small archive must not read as huge."""
    big = b"<?xml version=\"1.0\"?>\n<HealthData>\n" + (
        b' <Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone"'
        b' unit="count" startDate="2023-05-13 15:30:45 +0200"'
        b' endDate="2023-05-13 15:35:45 +0200" value="1"/>\n' * 2000
    ) + b"</HealthData>\n"
    payload = gzip.compress(big)
    assert len(payload) < len(big) / 5, "test payload should compress well"

    r = client.post(
        "/api/apple-health/import-async",
        files={"file": ("export.xml.gz", payload, "application/octet-stream")},
    )
    assert r.status_code == 202, r.text
    assert r.json()["size_mb"] == round(len(payload) / (1024 * 1024), 1)
