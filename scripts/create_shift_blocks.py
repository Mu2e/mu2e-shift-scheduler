#!/usr/bin/env python3
"""
Interactively create a shift-block CSV file.

The generated CSV is compatible with the scheduler loader. Extra columns are
included so the block structure is readable to humans and downstream tools.
"""
import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler.blocks import WEEKDAYS, WEEKDAY_NAMES, slugify, week_start  # noqa: E402


def prompt_text(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("Enter a value.")


def prompt_int(label: str, minimum: int = 1, default: int | None = None) -> int:
    while True:
        raw = prompt_text(label, str(default) if default is not None else None)
        try:
            value = int(raw)
        except ValueError:
            print("Enter an integer.")
            continue
        if value < minimum:
            print(f"Enter a value >= {minimum}.")
            continue
        return value


def prompt_date(label: str) -> date:
    while True:
        raw = prompt_text(label)
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            print("Use YYYY-MM-DD.")


def prompt_time(label: str) -> str:
    while True:
        raw = prompt_text(label)
        try:
            datetime.strptime(raw, "%H:%M")
        except ValueError:
            print("Use HH:MM in 24-hour time.")
            continue
        return raw


def prompt_weekdays(label: str, expected_count: int) -> list[int]:
    while True:
        raw = prompt_text(label)
        parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
        unknown = [part for part in parts if part not in WEEKDAYS]
        if unknown:
            print(f"Unknown day name(s): {', '.join(unknown)}")
            continue

        days = []
        for part in parts:
            day = WEEKDAYS[part]
            if day not in days:
                days.append(day)
        days.sort()

        if len(days) != expected_count:
            print(f"Enter exactly {expected_count} distinct day(s).")
            continue
        return days


def build_rows(
    schedule_name: str,
    start_date: date,
    end_date: date,
    blocks: list[dict],
    shifts: list[dict],
) -> tuple[list[dict], int]:
    rows = []
    skipped_partial_blocks = 0
    slug = slugify(schedule_name)
    current_week = week_start(start_date)
    final_week = week_start(end_date)
    week_number = 1

    while current_week <= final_week:
        for block_index, block in enumerate(blocks, start=1):
            block_dates = [current_week + timedelta(days=day) for day in block["days"]]
            if any(day < start_date or day > end_date for day in block_dates):
                skipped_partial_blocks += 1
                continue

            block_start = min(block_dates)
            block_end = max(block_dates)
            day_names = ",".join(WEEKDAY_NAMES[day] for day in block["days"])
            block_slug = slugify(block["name"])

            for shift_index, shift in enumerate(shifts, start=1):
                shift_slug = slugify(shift["name"])
                rows.append({
                    "shift_id": f"{slug}-W{week_number:02d}-B{block_index}-S{shift_index}-{shift_slug}",
                    "schedule_name": schedule_name,
                    "week": week_number,
                    "block_number": block_index,
                    "block_name": block["name"],
                    "block_type": block_slug,
                    "days": day_names,
                    "date": block_start.isoformat(),
                    "date_end": block_end.isoformat(),
                    "start_time": shift["start_time"],
                    "end_time": shift["end_time"],
                    "shift_type": shift["name"],
                })

        current_week += timedelta(days=7)
        week_number += 1

    return rows, skipped_partial_blocks


def collect_schedule() -> tuple[str, date, date, list[dict], list[dict]]:
    schedule_name = prompt_text("Schedule name")
    start_date = prompt_date("Schedule start date (YYYY-MM-DD)")
    while True:
        end_date = prompt_date("Schedule stop date (YYYY-MM-DD)")
        if end_date >= start_date:
            break
        print("Stop date must be on or after the start date.")

    blocks_per_week = prompt_int("Number of shift blocks per week", minimum=1)
    shifts_per_day = prompt_int("Number of shifts per day", minimum=1)

    blocks = []
    for index in range(1, blocks_per_week + 1):
        print()
        print(f"Block {index}")
        name = prompt_text("  Block name", f"block-{index}")
        days_per_block = prompt_int("  Number of days in this block", minimum=1)
        days = prompt_weekdays(
            "  Days of week for this block (comma-separated, e.g. Mon,Tue,Wed)",
            days_per_block,
        )
        blocks.append({"name": name, "days": days})

    shifts = []
    for index in range(1, shifts_per_day + 1):
        print()
        print(f"Shift {index}")
        name = prompt_text("  Shift name", f"shift-{index}")
        start_time = prompt_time("  Start time (HH:MM)")
        end_time = prompt_time("  Stop time (HH:MM)")
        shifts.append({"name": name, "start_time": start_time, "end_time": end_time})

    return schedule_name, start_date, end_date, blocks, shifts


def default_output_path(schedule_name: str) -> Path:
    return Path("sample_data") / f"{slugify(schedule_name)}_shift_blocks.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactively create a scheduler-compatible shift-block CSV.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="CSV",
        help="Output CSV path. Defaults to sample_data/<schedule-name>_shift_blocks.csv.",
    )
    args = parser.parse_args()

    schedule_name, start_date, end_date, blocks, shifts = collect_schedule()
    output_path = Path(args.output) if args.output else default_output_path(schedule_name)

    rows, skipped = build_rows(schedule_name, start_date, end_date, blocks, shifts)
    if not rows:
        raise SystemExit("No complete blocks were generated for the requested date range.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "shift_id",
        "schedule_name",
        "week",
        "block_number",
        "block_name",
        "block_type",
        "days",
        "date",
        "date_end",
        "start_time",
        "end_time",
        "shift_type",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"Wrote {len(rows)} shift block rows to {output_path}")
    if skipped:
        print(f"Skipped {skipped} partial block(s) outside the schedule date range.")


if __name__ == "__main__":
    main()
