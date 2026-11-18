"""Recurrence rules: once / daily / weekly / monthly, in a real timezone (IST = UTC+5:30)."""
from datetime import date, datetime, time, timezone

import pytest

from app.utils.scheduling import Frequency, Recurrence, describe, next_run_after

IST = "Asia/Kolkata"
# Wednesday 18 Nov 2026, 06:30 IST
NOW = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_daily_later_today_and_tomorrow():
    assert next_run_after(Recurrence(Frequency.DAILY, time(8, 0), IST), NOW) == utc(2026, 11, 18, 2, 30)
    assert next_run_after(Recurrence(Frequency.DAILY, time(6, 0), IST), NOW) == utc(2026, 11, 19, 0, 30)


def test_once_future_and_past():
    rec = Recurrence(Frequency.ONCE, time(9, 15), IST, run_date=date(2026, 11, 25))
    assert next_run_after(rec, NOW) == utc(2026, 11, 25, 3, 45)
    past = Recurrence(Frequency.ONCE, time(6, 0), IST, run_date=date(2026, 11, 18))
    assert next_run_after(past, NOW) is None  # already passed -> no next run


def test_weekly_picks_next_matching_weekday():
    # Mon(0), Fri(4); today is Wednesday -> Friday 20 Nov
    rec = Recurrence(Frequency.WEEKLY, time(8, 0), IST, days_of_week=(0, 4))
    assert next_run_after(rec, NOW) == utc(2026, 11, 20, 2, 30)
    # Wednesday itself, later today
    assert next_run_after(Recurrence(Frequency.WEEKLY, time(8, 0), IST, days_of_week=(2,)), NOW) == utc(2026, 11, 18, 2, 30)
    # Wednesday but the time passed -> next Wednesday
    assert next_run_after(Recurrence(Frequency.WEEKLY, time(6, 0), IST, days_of_week=(2,)), NOW) == utc(2026, 11, 25, 0, 30)


def test_monthly_this_month_next_month_and_clamping():
    assert next_run_after(Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=20), NOW) == utc(2026, 11, 20, 2, 30)
    assert next_run_after(Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=5), NOW) == utc(2026, 12, 5, 2, 30)
    # 31st in November (30 days) -> clamps to 30 Nov
    assert next_run_after(Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=31), NOW) == utc(2026, 11, 30, 2, 30)
    # 30th in February 2027 -> 28 Feb (after the Nov/Dec/Jan runs)
    after_jan = utc(2027, 1, 31, 0, 0)
    assert next_run_after(Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=30), after_jan) == utc(2027, 2, 28, 2, 30)


def test_dst_weekly_new_york():
    # 8:00 New York on a summer Monday is 12:00 UTC (EDT)
    rec = Recurrence(Frequency.WEEKLY, time(8, 0), "America/New_York", days_of_week=(0,))
    assert next_run_after(rec, utc(2026, 7, 1, 0, 0)).hour == 12


@pytest.mark.parametrize(
    "rec",
    [
        Recurrence(Frequency.ONCE, time(8, 0), IST),
        Recurrence(Frequency.WEEKLY, time(8, 0), IST, days_of_week=()),
        Recurrence(Frequency.WEEKLY, time(8, 0), IST, days_of_week=(7,)),
        Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=0),
        Recurrence(Frequency.DAILY, time(8, 0), "Mars/Base"),
    ],
)
def test_invalid_recurrences(rec):
    with pytest.raises(ValueError):
        rec.validate()


def test_describe():
    assert describe(Recurrence(Frequency.WEEKLY, time(8, 0), IST, days_of_week=(4, 0))) == "Weekly on Mon, Fri at 08:00"
    assert describe(Recurrence(Frequency.MONTHLY, time(8, 0), IST, day_of_month=22)) == "Monthly on the 22nd at 08:00"
