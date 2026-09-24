"""ADMIN_EMAILS / DEMO_EMAILS as they are typed into the Railway dashboard.

A plain address used to make pydantic-settings raise at import time, which
means the API does not start at all.
"""
import pytest

from app.config import Settings


@pytest.mark.parametrize("raw,expected", [
    ("me@example.com", ["me@example.com"]),
    ("a@x.com, b@y.com", ["a@x.com", "b@y.com"]),
    ('["a@x.com", "b@y.com"]', ["a@x.com", "b@y.com"]),
    ("", []),
])
def test_admin_emails_accept_what_people_type(monkeypatch, raw, expected):
    monkeypatch.setenv("ADMIN_EMAILS", raw)
    assert Settings().ADMIN_EMAILS == expected


def test_demo_emails_too(monkeypatch):
    monkeypatch.setenv("DEMO_EMAILS", "demo@example.com")
    assert Settings().DEMO_EMAILS == ["demo@example.com"]
