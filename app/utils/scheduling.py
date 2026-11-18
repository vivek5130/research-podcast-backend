"""Timezone-aware scheduling rules: once, daily, weekly, monthly.

A scheduler stores *local* wall-clock settings (``run_time`` such as 08:00, an IANA
``timezone`` such as Asia/Kolkata, plus the frequency-specific fields). We persist
the resulting ``next_run_at`` in UTC so the tick only needs
``WHERE next_run_at <= now()``. After each run the tick calls ``next_run_after``
again to advance it, so this module is the single source of truth for "when next".

All maths happens on local dates resolved through ``zoneinfo``, so daylight-saving
changes are handled.
"""
import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Frequency(StrEnum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # index 0 = Monday (datetime.weekday())


@dataclass(frozen=True)
class Recurrence:
    """When a scheduler runs.

    * once    - on ``run_date`` at ``run_time``
    * daily   - every day at ``run_time``
    * weekly  - on each weekday in ``days_of_week`` (0 = Mon ... 6 = Sun) at ``run_time``
    * monthly - on ``day_of_month`` (clamped to the month's last day) at ``run_time``
    """

    frequency: Frequency
    run_time: time
    timezone: str
    run_date: date | None = None
    days_of_week: tuple[int, ...] | None = None
    day_of_month: int | None = None

    def validate(self) -> "Recurrence":
        """Raise ValueError if the fields don't make sense for the frequency."""
        validate_timezone(self.timezone)
        if self.frequency is Frequency.ONCE and self.run_date is None:
            raise ValueError("run_date is required for a one-time schedule")
        if self.frequency is Frequency.WEEKLY:
            days = self.days_of_week or ()
            if not days or any(d not in range(7) for d in days):
                raise ValueError("days_of_week must contain 1-7 weekdays (0 = Monday ... 6 = Sunday)")
        if self.frequency is Frequency.MONTHLY and not (self.day_of_month and 1 <= self.day_of_month <= 31):
            raise ValueError("day_of_month must be between 1 and 31")
        return self


def validate_timezone(tz: str) -> str:
    """Return ``tz`` unchanged if it is a known IANA timezone, else raise ValueError."""
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {tz}") from exc
    return tz


def _at(day: date, run_time: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, run_time, tzinfo=zone)


def _monthly_day(year: int, month: int, day_of_month: int) -> date:
    """The run date in a month: ``day_of_month``, or the last day if the month is shorter."""
    return date(year, month, min(day_of_month, calendar.monthrange(year, month)[1]))


def next_run_after(rec: Recurrence, after: datetime | None = None) -> datetime | None:
    """Next UTC instant strictly after ``after`` that matches ``rec``.

    Returns None for a one-time schedule whose moment has already passed.
    """
    zone = ZoneInfo(rec.timezone)
    after = after or datetime.now(timezone.utc)
    local_now = after.astimezone(zone)
    today = local_now.date()

    candidate: datetime | None = None
    if rec.frequency is Frequency.ONCE:
        at = _at(rec.run_date, rec.run_time, zone)  # type: ignore[arg-type]
        candidate = at if at > local_now else None

    elif rec.frequency is Frequency.DAILY:
        candidate = _at(today, rec.run_time, zone)
        if candidate <= local_now:
            candidate = _at(today + timedelta(days=1), rec.run_time, zone)

    elif rec.frequency is Frequency.WEEKLY:
        days = set(rec.days_of_week or ())
        for offset in range(8):  # today .. same weekday next week
            day = today + timedelta(days=offset)
            if day.weekday() in days and (at := _at(day, rec.run_time, zone)) > local_now:
                candidate = at
                break

    elif rec.frequency is Frequency.MONTHLY:
        year, month = today.year, today.month
        for _ in range(13):
            at = _at(_monthly_day(year, month, rec.day_of_month), rec.run_time, zone)  # type: ignore[arg-type]
            if at > local_now:
                candidate = at
                break
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    return candidate.astimezone(timezone.utc) if candidate else None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(rec: Recurrence) -> str:
    """Human summary, e.g. "Weekly on Mon, Wed, Fri at 08:00"."""
    t = rec.run_time.strftime("%H:%M")
    if rec.frequency is Frequency.ONCE:
        return f"Once on {rec.run_date:%a %d %b %Y} at {t}"
    if rec.frequency is Frequency.WEEKLY:
        days = sorted(rec.days_of_week or ())
        label = "every day" if len(days) == 7 else ", ".join(WEEKDAY_NAMES[d] for d in days)
        return f"Weekly on {label} at {t}"
    if rec.frequency is Frequency.MONTHLY:
        return f"Monthly on the {_ordinal(rec.day_of_month or 1)} at {t}"
    return f"Daily at {t}"
