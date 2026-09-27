"""DATABASE_URL as Railway and people type it.

SQLAlchemy 2.1 maps a bare postgresql:// to psycopg 3, which is not installed,
so the API started without a database. The driver that is installed is named
explicitly.
"""
import pytest

from app.database.models import get_database_path


@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
    ("postgresql://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
    ("postgresql+psycopg2://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
])
def test_postgres_urls_use_the_installed_driver(monkeypatch, raw, expected):
    monkeypatch.setenv("DATABASE_URL", raw)
    assert get_database_path() == expected
