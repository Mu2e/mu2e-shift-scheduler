"""Tests for scheduler.blocks pure date/shift logic."""
from datetime import date

from scheduler.blocks import (
    add_months,
    expand_shift_days,
    parse_days_text,
    slugify,
    week_start,
)


def test_slugify():
    assert slugify("Run Coordinators") == "run-coordinators"
    assert slugify("  DAQ Expert (Night) ") == "daq-expert-night"
    assert slugify("!!!") == "schedule"


def test_week_start_is_monday():
    assert week_start(date(2026, 5, 7)) == date(2026, 5, 4)  # Thu -> Mon
    assert week_start(date(2026, 5, 4)) == date(2026, 5, 4)  # Mon stays
    assert week_start(date(2026, 5, 10)) == date(2026, 5, 4)  # Sun -> Mon


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)
    assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # leap year


def test_parse_days_text():
    assert parse_days_text("Mon,Tue,Wed") == [0, 1, 2]
    assert parse_days_text("fri, sat , SUN") == [4, 5, 6]
    assert parse_days_text("") == []
    assert parse_days_text("bogus,Mon") == [0]


def test_expand_single_day():
    assert expand_shift_days("2026-04-06") == [date(2026, 4, 6)]
    assert expand_shift_days("2026-04-06", "") == [date(2026, 4, 6)]
    assert expand_shift_days("2026-04-06", "2026-04-06") == [date(2026, 4, 6)]


def test_expand_multi_day_range():
    days = expand_shift_days("2026-04-06", "2026-04-09")
    assert days == [date(2026, 4, 6), date(2026, 4, 7), date(2026, 4, 8), date(2026, 4, 9)]


def test_expand_with_weekday_filter():
    # Mon 2026-04-06 through Sun 2026-04-12, Mon-Thu block
    days = expand_shift_days("2026-04-06", "2026-04-12", "Mon,Tue,Wed,Thu")
    assert days == [date(2026, 4, 6), date(2026, 4, 7), date(2026, 4, 8), date(2026, 4, 9)]


def test_expand_weekend_block_crossing_week():
    # Fri-Sun block
    days = expand_shift_days("2026-04-10", "2026-04-12", "Fri,Sat,Sun")
    assert days == [date(2026, 4, 10), date(2026, 4, 11), date(2026, 4, 12)]


def test_expand_invalid_date_returns_empty():
    assert expand_shift_days("") == []
    assert expand_shift_days("not-a-date") == []


def test_expand_invalid_end_degrades_to_single_day():
    assert expand_shift_days("2026-04-06", "garbage") == [date(2026, 4, 6)]
    # end before start treated as single day
    assert expand_shift_days("2026-04-06", "2026-04-01") == [date(2026, 4, 6)]


def test_expand_filter_matching_nothing_falls_back_to_range():
    # Mon-Tue range but a Sat-only filter: fall back to the full range
    days = expand_shift_days("2026-04-06", "2026-04-07", "Sat")
    assert days == [date(2026, 4, 6), date(2026, 4, 7)]


def test_expand_range_is_capped():
    days = expand_shift_days("2026-01-01", "2027-12-31")
    assert len(days) <= 63
