"""
Builds calendar view payloads (month / week / today) from shift rows and
assignments, expanding multi-day shift blocks onto every covered day and
attaching contact info to assigned people.
"""
import calendar as py_calendar
from datetime import date, timedelta

from app import store
from scheduler.blocks import expand_shift_days

_EMPTY_MARKERS = {"", "UNASSIGNED"}


def _person_payload(row: dict, contact_cache: dict) -> dict | None:
    name = str(row.get("person", "") or "").strip()
    if name.upper() in _EMPTY_MARKERS:
        return None
    if name not in contact_cache:
        contact = store.get_contact(name) or {}
        contact_cache[name] = contact
    contact = contact_cache[name]
    return {
        "name": name,
        "email": str(row.get("email", "") or "").strip() or contact.get("email", ""),
        "phone": str(row.get("phone", "") or "").strip() or contact.get("phone", ""),
        "institution": str(row.get("institution", "") or "").strip() or contact.get("institution", ""),
    }


def build_entries(rows: list[dict], assignments: dict | None = None) -> dict[str, list[dict]]:
    """Expand shift rows to per-day calendar entries.

    rows: schedule_shifts rows or legacy result/CSV dicts. assignments (when
    given) maps shift_id -> assignment row and overrides inline person fields.
    Returns {iso_date: [entry, ...]} sorted by start time within each day.
    """
    entries: dict[str, list[dict]] = {}
    contact_cache: dict = {}
    for row in rows:
        merged = dict(row)
        if assignments is not None:
            merged.update(assignments.get(str(row.get("shift_id", "")), {}))
        days = expand_shift_days(
            str(row.get("date", "")),
            str(row.get("date_end", "") or ""),
            str(row.get("days", "") or ""),
        )
        if not days:
            continue
        shift_name = (
            str(row.get("shift_name", "") or "").strip()
            or str(row.get("shift_type", "") or "").strip()
            or str(row.get("block_type", "") or "").strip()
            or str(row.get("shift_id", "") or "").strip()
        )
        person = _person_payload(merged, contact_cache)
        total = len(days)
        for index, day in enumerate(days, start=1):
            entries.setdefault(day.isoformat(), []).append({
                "shift_id": str(row.get("shift_id", "")),
                "shift_name": shift_name,
                "start_time": str(row.get("start_time", "") or ""),
                "end_time": str(row.get("end_time", "") or ""),
                "person": person,
                "is_preferred": merged.get("is_preferred", True),
                "span": {
                    "is_multi": total > 1,
                    "day_index": index,
                    "total_days": total,
                },
            })
    for day_entries in entries.values():
        day_entries.sort(key=lambda e: (e["start_time"], e["shift_name"], e["shift_id"]))
    return entries


def _day_payload(entries: dict, day: date, month: int | None = None) -> dict:
    date_text = day.isoformat()
    return {
        "date": day,
        "date_text": date_text,
        "in_month": (day.month == month) if month else True,
        "is_today": day == date.today(),
        "entries": entries.get(date_text, []),
    }


def month_grid(entries: dict, year: int, month: int) -> dict:
    weeks = []
    for week in py_calendar.Calendar(firstweekday=6).monthdatescalendar(year, month):
        weeks.append([_day_payload(entries, day, month) for day in week])
    return {
        "label": date(year, month, 1).strftime("%B %Y"),
        "weeks": weeks,
    }


def _week_sunday(anchor: date) -> date:
    # Sunday-first weeks, matching the month grid.
    return anchor - timedelta(days=(anchor.weekday() + 1) % 7)


def week_grid(entries: dict, anchor: date) -> dict:
    start = _week_sunday(anchor)
    days = [_day_payload(entries, start + timedelta(days=i)) for i in range(7)]
    end = start + timedelta(days=6)
    if start.month == end.month:
        label = f"Week of {start.strftime('%b %-d')} – {end.strftime('%-d, %Y')}"
    else:
        label = f"Week of {start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    return {"label": label, "weeks": [days]}


def day_agenda(entries: dict, anchor: date) -> dict:
    return {
        "label": anchor.strftime("%A, %B %-d, %Y"),
        "day": _day_payload(entries, anchor),
    }


def prev_next_anchors(view: str, anchor: date) -> tuple[str, str]:
    """Anchor dates (ISO) for the previous / next navigation links."""
    if view == "week":
        return (anchor - timedelta(days=7)).isoformat(), (anchor + timedelta(days=7)).isoformat()
    if view == "today":
        return (anchor - timedelta(days=1)).isoformat(), (anchor + timedelta(days=1)).isoformat()
    # month: first day of the previous / next month
    first = anchor.replace(day=1)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return prev_month.isoformat(), next_month.isoformat()
