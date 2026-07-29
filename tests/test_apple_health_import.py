"""Import endpoint must reject the wrong file clearly instead of failing opaquely.

export.zip contains both export.xml (measurements) and export_cda.xml (clinical
documents, no <Record> elements). Uploading the CDA file used to be accepted and
reported as a successful import of zero records.
"""
import pytest
from fastapi.testclient import TestClient

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


def _post(client, filename, payload):
    return client.post(
        "/api/apple-health/import",
        files={"file": (filename, payload, "text/xml")},
    )


def test_cda_file_is_rejected_with_guidance(client):
    r = _post(client, "export_cda.xml", CDA_XML)
    assert r.status_code == 400
    assert "export.xml" in r.json()["detail"]


def test_non_xml_is_rejected(client):
    r = _post(client, "notes.txt", EXPORT_XML)
    assert r.status_code == 400


def test_xml_without_records_is_rejected(client):
    r = _post(client, "empty.xml", b"<HealthData/>")
    assert r.status_code == 400
    assert "export.xml" in r.json()["detail"]


def test_valid_export_imports(client):
    r = _post(client, "export.xml", EXPORT_XML)
    assert r.status_code == 200
    assert r.json()["success"] is True
