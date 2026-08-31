"""Tests for scheduler.blocks.generate_schedule_rows and CSV serialization."""
import csv
import io
from datetime import date

import pytest

from scheduler.blocks import (
    BLOCK_CSV_FIELDNAMES,
    ShiftSpec,
    generate_schedule_rows,
    rows_to_block_csv_string,
)
from scheduler.loader import load_shifts


def _weekly_block_specs():
    return [
        ShiftSpec(name="Weekday Day", start_time="08:00", end_time="16:00",
                  repetition="week", weekdays=[0, 1, 2, 3], weight=1.0),
        ShiftSpec(name="Weekend Day", start_time="08:00", end_time="16:00",
                  repetition="week", weekdays=[4, 5, 6], weight=1.5),
    ]


def test_weekly_blocks_shape():
    # Mon 2026-05-04 .. Sun 2026-05-17: two full weeks
    rows, skipped = generate_schedule_rows(
        "Run Coordinators", date(2026, 5, 4), date(2026, 5, 17), _weekly_block_specs()
    )
    assert skipped == 0
    assert len(rows) == 4  # 2 specs x 2 weeks
    weekday_rows = [r for r in rows if r["shift_type"] == "Weekday Day"]
    assert weekday_rows[0]["date"] == "2026-05-04"
    assert weekday_rows[0]["date_end"] == "2026-05-07"
    assert weekday_rows[0]["days"] == "Mon,Tue,Wed,Thu"
    assert weekday_rows[0]["schedule_name"] == "Run Coordinators"
    assert weekday_rows[0]["points"] == 1.0
    assert weekday_rows[0]["shift_id"].startswith("run-coordinators-W01-")
    weekend_rows = [r for r in rows if r["shift_type"] == "Weekend Day"]
    assert weekend_rows[0]["date"] == "2026-05-08"
    assert weekend_rows[0]["date_end"] == "2026-05-10"


def test_partial_occurrences_skipped():
    # Range starts on Wednesday: the first weekday block spills before start
    rows, skipped = generate_schedule_rows(
        "Test", date(2026, 5, 6), date(2026, 5, 17), _weekly_block_specs()
    )
    assert skipped >= 1
    dates = {r["date"] for r in rows if r["shift_type"] == "Weekday Day"}
    assert dates == {"2026-05-11"}


def test_daily_repetition():
    spec = ShiftSpec(name="Day", start_time="08:00", end_time="16:00",
                     length_days=1, repetition="day", weight=1.0)
    rows, skipped = generate_schedule_rows("Daily", date(2026, 5, 4), date(2026, 5, 8), [spec])
    assert skipped == 0
    assert [r["date"] for r in rows] == [
        "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
    ]
    assert all(r["date"] == r["date_end"] for r in rows)


def test_two_week_repetition():
    spec = ShiftSpec(name="Oncall", start_time="00:00", end_time="23:59",
                     length_days=7, repetition="2week")
    rows, _ = generate_schedule_rows("Oncall", date(2026, 5, 4), date(2026, 5, 31), [spec])
    assert [r["date"] for r in rows] == ["2026-05-04", "2026-05-18"]
    assert rows[0]["date_end"] == "2026-05-10"
    assert rows[0]["points"] == ""  # no weight -> loader defaults apply


def test_month_repetition_clamps():
    spec = ShiftSpec(name="Monthly", start_time="08:00", end_time="16:00",
                     length_days=1, repetition="month")
    rows, _ = generate_schedule_rows("Monthly", date(2026, 1, 31), date(2026, 4, 30), [spec])
    assert [r["date"] for r in rows] == ["2026-01-31", "2026-02-28", "2026-03-28", "2026-04-28"]


def test_unique_shift_ids():
    rows, _ = generate_schedule_rows(
        "Fall 2026", date(2026, 5, 4), date(2026, 6, 28), _weekly_block_specs()
    )
    ids = [r["shift_id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_bad_repetition_rejected():
    spec = ShiftSpec(name="Bad", start_time="08:00", end_time="16:00", repetition="fortnight")
    with pytest.raises(ValueError):
        generate_schedule_rows("Bad", date(2026, 5, 4), date(2026, 5, 10), [spec])


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        generate_schedule_rows("Bad", date(2026, 5, 10), date(2026, 5, 4), _weekly_block_specs())


def test_csv_round_trip_through_loader(tmp_path):
    rows, _ = generate_schedule_rows(
        "Fall 2026", date(2026, 5, 4), date(2026, 5, 17), _weekly_block_specs()
    )
    text = rows_to_block_csv_string(rows)

    parsed = list(csv.DictReader(io.StringIO(text)))
    assert list(parsed[0].keys()) == BLOCK_CSV_FIELDNAMES

    path = tmp_path / "generated.csv"
    path.write_text(text, encoding="utf-8")
    shifts = load_shifts(str(path), {})
    assert len(shifts) == len(rows)
    by_id = {s.shift_id: s for s in shifts}
    weekday = next(r for r in rows if r["shift_type"] == "Weekday Day")
    assert by_id[weekday["shift_id"]].points == 1.0
