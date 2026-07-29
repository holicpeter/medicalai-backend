"""Tests for Apple Health date parsing.

The original parser used string splitting that truncated any date whose
month or day segment started with '0' (Jan-Sep, days 01-09) and silently
substituted datetime.now() — corrupting most imported records.
"""
from datetime import datetime

from app.api.apple_health import parse_apple_health_date


def test_summer_month_with_positive_offset():
    assert parse_apple_health_date('2023-05-13 15:30:45 +0200') == datetime(2023, 5, 13, 15, 30, 45)


def test_winter_month_with_positive_offset():
    assert parse_apple_health_date('2023-12-13 15:30:45 +0100') == datetime(2023, 12, 13, 15, 30, 45)


def test_month_and_day_starting_with_zero():
    assert parse_apple_health_date('2024-01-05 08:05:09 +0100') == datetime(2024, 1, 5, 8, 5, 9)


def test_negative_utc_offset():
    assert parse_apple_health_date('2022-08-04 00:00:00 -0500') == datetime(2022, 8, 4, 0, 0, 0)


def test_no_timezone():
    assert parse_apple_health_date('2023-05-13 15:30:45') == datetime(2023, 5, 13, 15, 30, 45)


def test_invalid_input_returns_none_not_now():
    assert parse_apple_health_date('garbage') is None
    assert parse_apple_health_date('') is None
