"""Tests for DATABASE_URL validation.

A Supabase project REST URL (https://...) was configured as DATABASE_URL in
production. SQLAlchemy failed deep inside engine creation with
"Can't load plugin: sqlalchemy.dialects:https", the error was swallowed at
startup, and every database-backed endpoint silently returned errors.
"""
import os

import pytest

from app.database.models import get_database_path


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_rejects_https_project_url(monkeypatch, clean_env):
    monkeypatch.setenv("DATABASE_URL", "https://abcdef.supabase.co")
    with pytest.raises(ValueError, match="unusable scheme"):
        get_database_path()


def test_rewrites_legacy_postgres_prefix(monkeypatch, clean_env):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert get_database_path() == "postgresql://u:p@host:5432/db"


def test_accepts_postgresql_url(monkeypatch, clean_env):
    url = "postgresql://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", url)
    assert get_database_path() == url


def test_accepts_driver_qualified_url(monkeypatch, clean_env):
    url = "postgresql+psycopg2://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", url)
    assert get_database_path() == url


def test_falls_back_to_sqlite(clean_env):
    assert get_database_path().startswith("sqlite:///")
