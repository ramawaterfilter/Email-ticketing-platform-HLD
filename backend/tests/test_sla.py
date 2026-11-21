"""The business-minutes engine: evenings, weekends, holidays, multi-day."""

from datetime import datetime, timedelta, timezone

from app.tickets.sla import _load_hours, add_business_minutes, business_minutes_between

UTC = timezone.utc
# Mon-Fri 09:00-17:00 UTC (8h days), with one Thursday holiday.
HOURS = _load_hours(
    {
        "timezone": "UTC",
        "week": {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")},
        "holidays": ["2026-11-26"],  # the Thursday in the week starting Mon 2026-11-23
    }
)
MON = datetime(2026, 11, 23, tzinfo=UTC)


def test_guard_monday():
    assert MON.weekday() == 0 and datetime(2026, 11, 26, tzinfo=UTC).weekday() == 3


def at(day_offset: int, h: int, m: int) -> datetime:
    return (MON + timedelta(days=day_offset)).replace(hour=h, minute=m)


def test_add_within_same_day():
    assert add_business_minutes(at(0, 10, 0), 60, HOURS) == at(0, 11, 0)


def test_add_clamps_to_opening_time():
    assert add_business_minutes(at(0, 7, 0), 30, HOURS) == at(0, 9, 30)


def test_add_rolls_over_the_evening():
    # 16:30 Mon + 60: 30 min to 17:00, then 30 min into Tue morning.
    assert add_business_minutes(at(0, 16, 30), 60, HOURS) == at(1, 9, 30)


def test_add_skips_the_weekend():
    # Fri 16:30 + 60: 30 min Fri, then 30 min on the next Monday.
    assert add_business_minutes(at(4, 16, 30), 60, HOURS) == at(7, 9, 30)


def test_add_skips_a_holiday():
    # Wed 16:30 + 60: 30 min Wed, Thu is a holiday, 30 min Fri.
    assert add_business_minutes(at(2, 16, 30), 60, HOURS) == at(4, 9, 30)


def test_add_spans_many_days():
    # 960 min = two full 8h days from Mon 09:00, landing at the close of Tue.
    assert add_business_minutes(at(0, 9, 0), 960, HOURS) == at(1, 17, 0)
    # One more minute tips into Wed morning.
    assert add_business_minutes(at(0, 9, 0), 961, HOURS) == at(2, 9, 1)


def test_between_same_day():
    assert business_minutes_between(at(0, 10, 0), at(0, 12, 0), HOURS) == 120


def test_between_counts_only_business_time_overnight():
    # Mon 16:00 -> Tue 10:00: 1h Mon evening + 1h Tue morning.
    assert business_minutes_between(at(0, 16, 0), at(1, 10, 0), HOURS) == 120


def test_between_ignores_weekend():
    assert business_minutes_between(at(4, 16, 0), at(7, 10, 0), HOURS) == 120


def test_between_is_zero_when_end_before_start():
    assert business_minutes_between(at(1, 10, 0), at(0, 10, 0), HOURS) == 0
