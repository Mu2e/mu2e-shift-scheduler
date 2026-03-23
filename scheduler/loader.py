"""
Loads shifts and people from CSV files, and builds per-person scheduling constraints
from a YAML config file combined with CLI overrides.
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Shift:
    shift_id: str
    date: str
    start_time: str
    end_time: str
    points: float = 1.0


@dataclass
class Person:
    name: str
    institution: str = ""
    # Ordered list of preferred shift IDs, most preferred first.
    preferences: list = field(default_factory=list)


def _default_points(start_time: str, sp_config: dict) -> float:
    """Return default shift points based on start_time and shift_points config."""
    night_start = sp_config.get("night_start", "20:00")
    night_end   = sp_config.get("night_end",   "08:00")
    night_pts   = float(sp_config.get("night",   2.0))
    day_pts     = float(sp_config.get("default", 1.0))
    t = start_time[:5]  # normalise to HH:MM
    if night_start > night_end:  # window wraps midnight (e.g. 20:00–08:00)
        is_night = t >= night_start or t < night_end
    else:
        is_night = night_start <= t < night_end
    return night_pts if is_night else day_pts


def load_shifts(filepath: str, config: Optional[dict] = None) -> list:
    """Load shifts from a CSV file.

    Required columns: shift_id, date, start_time, end_time
    Optional column:  points  (decimal; defaults computed from config if absent)
    """
    sp_config = (config or {}).get("shift_points", {})
    shifts = []
    seen_ids: set = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing = {"shift_id", "date", "start_time", "end_time"} - fieldnames
        if missing:
            raise ValueError(f"Shifts CSV is missing required columns: {missing}")
        has_points_col = "points" in fieldnames
        for row in reader:
            sid = row["shift_id"].strip()
            if not sid:
                continue
            if sid in seen_ids:
                raise ValueError(f"Duplicate shift_id in shifts file: '{sid}'")
            seen_ids.add(sid)
            start = row["start_time"].strip()
            if has_points_col and row["points"].strip():
                pts = float(row["points"].strip())
            else:
                pts = _default_points(start, sp_config)
            shifts.append(
                Shift(
                    shift_id=sid,
                    date=row["date"].strip(),
                    start_time=start,
                    end_time=row["end_time"].strip(),
                    points=pts,
                )
            )
    if not shifts:
        raise ValueError("Shifts file contains no data rows.")
    return shifts


def load_people(filepath: str) -> list:
    """
    Load people from a CSV file.

    Required column:  name
    Optional column:  institution
    Remaining columns are treated as ordered preferred shift IDs (empty cells skipped).
    """
    _RESERVED = {"name", "institution"}
    people = []
    seen_names: set = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not reader.fieldnames[0].strip().lower().startswith("name"):
            raise ValueError("People CSV must have 'name' as the first column header.")
        has_institution = "institution" in (reader.fieldnames or [])
        pref_cols = [c for c in (reader.fieldnames or []) if c.strip().lower() not in _RESERVED]
        for row in reader:
            name = row["name"].strip()
            if not name:
                continue
            if name in seen_names:
                raise ValueError(f"Duplicate person name in people file: '{name}'")
            seen_names.add(name)
            institution = row.get("institution", "").strip() if has_institution else ""
            preferences = [row[c].strip() for c in pref_cols if row.get(c, "").strip()]
            people.append(Person(name=name, institution=institution, preferences=preferences))
    if not people:
        raise ValueError("People file contains no data rows.")
    return people


def load_config(filepath: str) -> dict:
    """Load YAML config file; returns empty dict if file does not exist."""
    path = Path(filepath)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate(shifts: list, people: list) -> None:
    """Validate cross-references: all preference shift IDs must exist in shifts."""
    shift_ids = {s.shift_id for s in shifts}
    for person in people:
        for pref in person.preferences:
            if pref not in shift_ids:
                raise ValueError(
                    f"Person '{person.name}' lists unknown shift '{pref}' as a preference. "
                    f"Check that shift IDs match between the two files."
                )


def build_constraints(people: list, config: dict, cli_overrides: Optional[dict] = None) -> dict:
    """
    Build a per-person constraint dict from config + CLI overrides.

    Returns:
        { person_name: {"target": int, "min": int, "max": int} }

    Resolution priority: CLI arg > per-person YAML override > global YAML default.
    """
    g = config.get("global", {})
    defaults = {
        "target": float(g.get("target_points_per_person", g.get("target_shifts_per_person", 3))),
        "min":    float(g.get("min_points_per_person",    g.get("min_shifts_per_person",    1))),
        "max":    float(g.get("max_points_per_person",    g.get("max_shifts_per_person",    5))),
    }

    # Apply CLI overrides to global defaults
    if cli_overrides:
        for key in ("target", "min", "max"):
            if cli_overrides.get(key) is not None:
                defaults[key] = cli_overrides[key]

    # Validate defaults
    if defaults["min"] > defaults["max"]:
        raise ValueError(
            f"min_shifts ({defaults['min']}) cannot exceed max_shifts ({defaults['max']})."
        )
    if not (defaults["min"] <= defaults["target"] <= defaults["max"]):
        raise ValueError(
            f"target_shifts ({defaults['target']}) must be between "
            f"min ({defaults['min']}) and max ({defaults['max']})."
        )

    # Per-person YAML overrides
    per_person: dict = {}
    for override in config.get("overrides", []):
        pname = override.get("name", "").strip()
        if pname:
            per_person[pname] = {
                "target": float(override.get("target", defaults["target"])),
                "min":    float(override.get("min",    defaults["min"])),
                "max":    float(override.get("max",    defaults["max"])),
            }

    result: dict = {}
    for person in people:
        if person.name in per_person:
            result[person.name] = per_person[person.name]
        else:
            result[person.name] = dict(defaults)
    return result
