"""
Pure date / shift-block logic shared by the web Shift Setup page, the calendar
views, and the interactive scripts/create_shift_blocks.py generator.

No Flask or database imports belong here: everything in this module operates
on plain values so it can be unit tested directly.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta


WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

REPETITIONS = ("day", "week", "2week", "month")

# Guard against corrupt CSVs (a bad date_end could otherwise expand a single
# shift into thousands of calendar entries).
MAX_EXPANSION_DAYS = 62

BLOCK_CSV_FIELDNAMES = [
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
    "points",
]


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return slug or "schedule"


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def add_months(day: date, months: int) -> date:
    """Advance by calendar months, clamping the day-of-month (Jan 31 -> Feb 28)."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp to the last day of the target month.
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def parse_iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_days_text(days_text: str) -> list[int]:
    """Parse a 'Mon,Tue,Wed' style list into sorted weekday numbers (0=Mon)."""
    days: list[int] = []
    for part in str(days_text or "").split(","):
        key = part.strip().lower()
        if key in WEEKDAYS and WEEKDAYS[key] not in days:
            days.append(WEEKDAYS[key])
    return sorted(days)


def expand_shift_days(date_text: str, date_end_text: str = "", days_text: str = "") -> list[date]:
    """Return every calendar day a shift covers.

    A single-day shift (blank/invalid date_end) yields one date. A multi-day
    block yields every day in [date, date_end], optionally filtered by the
    block-CSV ``days`` weekday list ("Mon,Tue,Wed"). A weekday filter that
    matches nothing falls back to the full range rather than hiding the shift.
    """
    start = parse_iso_date(date_text)
    if start is None:
        return []
    end = parse_iso_date(date_end_text)
    if end is None or end < start:
        end = start
    if (end - start).days > MAX_EXPANSION_DAYS:
        end = start + timedelta(days=MAX_EXPANSION_DAYS)

    candidates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    weekday_filter = set(parse_days_text(days_text))
    if weekday_filter:
        filtered = [d for d in candidates if d.weekday() in weekday_filter]
        if filtered:
            return filtered
    return candidates


@dataclass
class BlockSpec:
    """One repeating block of days from the Shift Setup page.

    A block either names specific days of the week ("Weekdays" = Mon-Thu,
    "Weekends" = Fri-Sun) or, with no weekdays checked, covers length_days
    consecutive days from each occurrence anchor.
    """

    name: str                       # "Weekdays", "Weekends"
    length_days: int = 1            # block length for consecutive-day blocks
    weekdays: list = field(default_factory=list)  # checked days-of-week (0=Mon)


@dataclass
class ShiftSpec:
    """One shift period definition from the Shift Setup page."""

    name: str                       # "Day", "DAQ Expert Night"
    start_time: str                 # "08:00"
    end_time: str                   # "16:00"
    weight: float | None = None     # -> points column (None = loader defaults)


def _occurrence_dates(block: BlockSpec, anchor: date) -> list[date]:
    """Calendar days covered by one occurrence of a block starting at anchor."""
    if block.weekdays:
        base = week_start(anchor)
        return [base + timedelta(days=d) for d in sorted(block.weekdays)]
    return [anchor + timedelta(days=i) for i in range(max(1, block.length_days))]


def _next_anchor(repetition: str, anchor: date) -> date:
    if repetition == "day":
        return anchor + timedelta(days=1)
    if repetition == "2week":
        return anchor + timedelta(days=14)
    if repetition == "month":
        return add_months(anchor, 1)
    return anchor + timedelta(days=7)


def generate_schedule_rows(
    schedule_name: str,
    start_date: date,
    end_date: date,
    blocks: list,
    shifts: list,
    repetition: str = "week",
) -> tuple[list[dict], int]:
    """Generate block-CSV rows: every block x shift combination per occurrence.

    Returns (rows, skipped_partial_count). Occurrences whose covered days fall
    partly outside [start_date, end_date] are skipped and counted, matching the
    behavior of scripts/create_shift_blocks.py.
    """
    if end_date < start_date:
        raise ValueError("Schedule stop date must be on or after the start date.")
    if repetition not in REPETITIONS:
        raise ValueError(f"Unknown repetition rate '{repetition}'.")
    if not blocks:
        raise ValueError("Define at least one block.")
    if not shifts:
        raise ValueError("Define at least one shift.")
    if repetition == "day" and any(b.weekdays for b in blocks):
        raise ValueError(
            "Weekday selections conflict with daily repetition: a daily block "
            "would regenerate the same week every day. Use weekly repetition "
            "for day-of-week blocks."
        )

    slug = slugify(schedule_name)
    rows: list[dict] = []
    skipped = 0
    seen_ids: set = set()

    for block_index, block in enumerate(blocks, start=1):
        # Weekday-checkbox blocks are anchored to week starts so the checked
        # days always mean "those days of that week".
        anchor = week_start(start_date) if block.weekdays else start_date
        occurrence = 1
        while anchor <= end_date:
            covered = _occurrence_dates(block, anchor)
            if any(d < start_date or d > end_date for d in covered):
                skipped += 1
            else:
                block_start = min(covered)
                block_end = max(covered)
                day_names = ",".join(
                    WEEKDAY_NAMES[d] for d in sorted({c.weekday() for c in covered})
                )
                for shift_index, shift in enumerate(shifts, start=1):
                    shift_slug = slugify(shift.name)
                    shift_id = (
                        f"{slug}-W{occurrence:02d}-B{block_index}-S{shift_index}-{shift_slug}"
                    )
                    if shift_id in seen_ids:
                        raise ValueError(f"Duplicate generated shift_id '{shift_id}'.")
                    seen_ids.add(shift_id)
                    rows.append({
                        "shift_id": shift_id,
                        "schedule_name": schedule_name,
                        "week": occurrence,
                        "block_number": block_index,
                        "block_name": block.name,
                        "block_type": slugify(block.name),
                        "days": day_names,
                        "date": block_start.isoformat(),
                        "date_end": block_end.isoformat(),
                        "start_time": shift.start_time,
                        "end_time": shift.end_time,
                        "shift_type": shift.name,
                        "points": "" if shift.weight is None else shift.weight,
                    })
            next_anchor = _next_anchor(repetition, anchor)
            if next_anchor <= anchor:
                break
            anchor = next_anchor
            occurrence += 1

    return rows, skipped


def rows_to_block_csv_string(rows: list[dict]) -> str:
    """Serialize block rows to the CSV format consumed by scheduler.loader."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=BLOCK_CSV_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in BLOCK_CSV_FIELDNAMES})
    return buf.getvalue()
