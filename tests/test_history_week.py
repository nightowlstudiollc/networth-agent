"""Tests for monday_of helper."""

from datetime import datetime
from zoneinfo import ZoneInfo


def test_monday_of_on_monday_returns_same_day():
    from history import monday_of

    mon = datetime(2026, 4, 13, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(mon) == "2026-04-13"


def test_monday_of_mid_week_returns_previous_monday():
    from history import monday_of

    wed = datetime(2026, 4, 15, 15, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(wed) == "2026-04-13"


def test_monday_of_sunday_returns_previous_monday():
    from history import monday_of

    sun = datetime(2026, 4, 19, 23, 59, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert monday_of(sun) == "2026-04-13"


def test_monday_of_honors_local_tz_not_utc():
    """A UTC Monday that is Sunday locally should bucket to local-Monday."""
    from history import monday_of

    # 2026-04-13 02:00 UTC = 2026-04-12 19:00 PDT (Sunday)
    utc_mon_local_sun = datetime(2026, 4, 13, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert monday_of(utc_mon_local_sun) == "2026-04-06"
