"""Tests for scheduler.blocks.generate_schedule_rows and CSV serialization."""
import csv
import io
from datetime import date

import pytest

from scheduler.blocks import (
    BLOCK_CSV_FIELDNAMES,
    BlockSpec,
    ShiftSpec,
    generate_schedule_rows,
    rows_to_block_csv_string,
)
from scheduler.loader import load_shifts


def _weekly_blocks():
    return [
        BlockSpec(name="Weekdays", weekdays=[0, 1, 2, 3]),
        BlockSpec(name="Weekends", weekdays=[4, 5, 6]),
    ]


def _day_shift():
    return [ShiftSpec(name="Day", start_time="08:00", end_time="16:00", weight=1.0)]


def test_weekly_blocks_shape():
    # Mon 2026-05-04 .. Sun 2026-05-17: two full weeks
    rows, skipped = generate_schedule_rows(
        "Run Coordinators", date(2026, 5, 4), date(2026, 5, 17),
        _weekly_blocks(), _day_shift(), "week",
    )
    assert skipped == 0
    assert len(rows) == 4  # 2 blocks x 1 shift x 2 weeks
    weekday_rows = [r for r in rows if r["block_name"] == "Weekdays"]
    assert weekday_rows[0]["date"] == "2026-05-04"
    assert weekday_rows[0]["date_end"] == "2026-05-07"
    assert weekday_rows[0]["days"] == "Mon,Tue,Wed,Thu"
    assert weekday_rows[0]["block_type"] == "weekdays"
    assert weekday_rows[0]["shift_type"] == "Day"
    assert weekday_rows[0]["schedule_name"] == "Run Coordinators"
    assert weekday_rows[0]["points"] == 1.0
    assert weekday_rows[0]["shift_id"].startswith("run-coordinators-W01-B1-S1-")
    weekend_rows = [r for r in rows if r["block_name"] == "Weekends"]
    assert weekend_rows[0]["date"] == "2026-05-08"
    assert weekend_rows[0]["date_end"] == "2026-05-10"
    assert weekend_rows[0]["shift_id"].startswith("run-coordinators-W01-B2-S1-")


def test_blocks_cross_multiple_shifts():
    shifts = [
        ShiftSpec(name="Day", start_time="08:00", end_time="20:00", weight=1.0),
        ShiftSpec(name="Night", start_time="20:00", end_time="08:00", weight=2.0),
    ]
    rows, _ = generate_schedule_rows(
        "Fall 2026", date(2026, 5, 4), date(2026, 5, 10),
        _weekly_blocks(), shifts, "week",
    )
    assert len(rows) == 4  # 2 blocks x 2 shifts x 1 week
    weekday_shift_types = {r["shift_type"] for r in rows if r["block_name"] == "Weekdays"}
    assert weekday_shift_types == {"Day", "Night"}
    night = next(r for r in rows if r["block_name"] == "Weekends" and r["shift_type"] == "Night")
    assert night["points"] == 2.0
    assert night["days"] == "Fri,Sat,Sun"


def test_partial_occurrences_skipped():
    # Range starts on Wednesday: the first weekday block spills before start
    rows, skipped = generate_schedule_rows(
        "Test", date(2026, 5, 6), date(2026, 5, 17),
        _weekly_blocks(), _day_shift(), "week",
    )
    assert skipped >= 1
    dates = {r["date"] for r in rows if r["block_name"] == "Weekdays"}
    assert dates == {"2026-05-11"}


def test_daily_repetition_consecutive_block():
    blocks = [BlockSpec(name="Single", length_days=1)]
    rows, skipped = generate_schedule_rows(
        "Daily", date(2026, 5, 4), date(2026, 5, 8), blocks, _day_shift(), "day",
    )
    assert skipped == 0
    assert [r["date"] for r in rows] == [
        "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
    ]
    assert all(r["date"] == r["date_end"] for r in rows)


def test_daily_repetition_rejects_weekday_blocks():
    with pytest.raises(ValueError, match="conflict with daily repetition"):
        generate_schedule_rows(
            "Bad", date(2026, 5, 4), date(2026, 5, 10),
            _weekly_blocks(), _day_shift(), "day",
        )


def test_two_week_repetition():
    blocks = [BlockSpec(name="Oncall Week", length_days=7)]
    shifts = [ShiftSpec(name="Oncall", start_time="00:00", end_time="23:59")]
    rows, _ = generate_schedule_rows(
        "Oncall", date(2026, 5, 4), date(2026, 5, 31), blocks, shifts, "2week",
    )
    assert [r["date"] for r in rows] == ["2026-05-04", "2026-05-18"]
    assert rows[0]["date_end"] == "2026-05-10"
    assert rows[0]["block_name"] == "Oncall Week"
    assert rows[0]["points"] == ""  # no weight -> loader defaults apply


def test_month_repetition_clamps():
    blocks = [BlockSpec(name="Monthly", length_days=1)]
    rows, _ = generate_schedule_rows(
        "Monthly", date(2026, 1, 31), date(2026, 4, 30), blocks, _day_shift(), "month",
    )
    assert [r["date"] for r in rows] == ["2026-01-31", "2026-02-28", "2026-03-28", "2026-04-28"]


def test_unique_shift_ids():
    shifts = [
        ShiftSpec(name="Day", start_time="08:00", end_time="20:00"),
        ShiftSpec(name="Night", start_time="20:00", end_time="08:00"),
    ]
    rows, _ = generate_schedule_rows(
        "Fall 2026", date(2026, 5, 4), date(2026, 6, 28),
        _weekly_blocks(), shifts, "week",
    )
    ids = [r["shift_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_bad_inputs_rejected():
    with pytest.raises(ValueError, match="repetition"):
        generate_schedule_rows("Bad", date(2026, 5, 4), date(2026, 5, 10),
                               _weekly_blocks(), _day_shift(), "fortnight")
    with pytest.raises(ValueError, match="on or after"):
        generate_schedule_rows("Bad", date(2026, 5, 10), date(2026, 5, 4),
                               _weekly_blocks(), _day_shift(), "week")
    with pytest.raises(ValueError, match="at least one block"):
        generate_schedule_rows("Bad", date(2026, 5, 4), date(2026, 5, 10),
                               [], _day_shift(), "week")
    with pytest.raises(ValueError, match="at least one shift"):
        generate_schedule_rows("Bad", date(2026, 5, 4), date(2026, 5, 10),
                               _weekly_blocks(), [], "week")


def test_csv_round_trip_through_loader(tmp_path):
    rows, _ = generate_schedule_rows(
        "Fall 2026", date(2026, 5, 4), date(2026, 5, 17),
        _weekly_blocks(), _day_shift(), "week",
    )
    text = rows_to_block_csv_string(rows)

    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == BLOCK_CSV_FIELDNAMES

    path = tmp_path / "generated.csv"
    path.write_text(text, encoding="utf-8")
    shifts = load_shifts(str(path), {})
    assert len(shifts) == len(rows)
    by_id = {s.shift_id: s for s in shifts}
    weekday = next(r for r in rows if r["block_name"] == "Weekdays")
    assert by_id[weekday["shift_id"]].points == 1.0
