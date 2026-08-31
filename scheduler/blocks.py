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
class ShiftSpec:
    """One shift period definition from the Shift Setup page."""

    name: str                       # "Day", "DAQ Expert Night"
    start_time: str                 # "08:00"
    end_time: str                   # "16:00"
    length_days: int = 1            # shift length in days (1 = single day)
    repetition: str = "week"        # day | week | 2week | month
    weekdays: list = field(default_factory=list)  # checked days-of-week (0=Mon)
    weight: float | None = None     # -> points column (None = loader defaults)


def _occurrence_dates(spec: ShiftSpec, anchor: date) -> list[date]:
    """Calendar days covered by one occurrence of a spec starting at anchor."""
    if spec.weekdays:
        base = week_start(anchor)
        return [base + timedelta(days=d) for d in sorted(spec.weekdays)]
    return [anchor + timedelta(days=i) for i in range(max(1, spec.length_days))]


def _next_anchor(spec: ShiftSpec, anchor: date) -> date:
    if spec.repetition == "day":
        return anchor + timedelta(days=1)
    if spec.repetition == "2week":
        return anchor + timedelta(days=14)
    if spec.repetition == "month":
        return add_months(anchor, 1)
    return anchor + timedelta(days=7)


def generate_schedule_rows(
    schedule_name: str,
    start_date: date,
    end_date: date,
    shift_specs: list,
) -> tuple[list[dict], int]:
    """Generate block-CSV rows for a schedule from Shift Setup specs.

    Returns (rows, skipped_partial_count). Occurrences whose covered days fall
    partly outside [start_date, end_date] are skipped and counted, matching the
    behavior of scripts/create_shift_blocks.py.
    """
    if end_date < start_date:
        raise ValueError("Schedule stop date must be on or after the start date.")
    for spec in shift_specs:
        if spec.repetition not in REPETITIONS:
            raise ValueError(f"Unknown repetition rate '{spec.repetition}'.")

    slug = slugify(schedule_name)
    rows: list[dict] = []
    skipped = 0
    seen_ids: set = set()

    for spec_index, spec in enumerate(shift_specs, start=1):
        # Weekday-checkbox blocks are anchored to week starts so the checked
        # days always mean "those days of that week".
        anchor = week_start(start_date) if spec.weekdays else start_date
        occurrence = 1
        while anchor <= end_date:
            covered = _occurrence_dates(spec, anchor)
            if any(d < start_date or d > end_date for d in covered):
                skipped += 1
            else:
                block_start = min(covered)
                block_end = max(covered)
                day_names = ",".join(
                    WEEKDAY_NAMES[d] for d in sorted({c.weekday() for c in covered})
                )
                shift_slug = slugify(spec.name)
                shift_id = f"{slug}-W{occurrence:02d}-B{spec_index}-S{spec_index}-{shift_slug}"
                if shift_id in seen_ids:
                    raise ValueError(f"Duplicate generated shift_id '{shift_id}'.")
                seen_ids.add(shift_id)
                rows.append({
                    "shift_id": shift_id,
                    "schedule_name": schedule_name,
                    "week": occurrence,
                    "block_number": spec_index,
                    "block_name": spec.name,
                    "block_type": shift_slug,
                    "days": day_names,
                    "date": block_start.isoformat(),
                    "date_end": block_end.isoformat(),
                    "start_time": spec.start_time,
                    "end_time": spec.end_time,
                    "shift_type": spec.name,
                    "points": "" if spec.weight is None else spec.weight,
                })
            next_anchor = _next_anchor(spec, anchor)
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
