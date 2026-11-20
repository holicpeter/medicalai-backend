"""The demo account must not drift into the past.

Its data is generated relative to the day the seed runs. Seeded once in
September, by November the activity overview (recent days) and the nutrition
diary (today) showed nothing. demo_refresh_loop re-seeds once data is stale.
"""
from datetime import date

from app.auth.bootstrap import demo_is_stale

TODAY = date(2026, 11, 20)


def test_fresh_data_is_kept():
    assert not demo_is_stale(date(2026, 11, 20), TODAY)
    assert not demo_is_stale(date(2026, 11, 18), TODAY)


def test_old_data_is_refreshed():
    assert demo_is_stale(date(2026, 11, 17), TODAY)
    assert demo_is_stale(date(2026, 9, 22), TODAY)


def test_no_data_is_refreshed():
    assert demo_is_stale(None, TODAY)
