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


@dataclass
class Person:
    name: str
    # Ordered list of preferred shift IDs, most preferred first.
    preferences: list = field(default_factory=list)


def load_shifts(filepath: str) -> list:
    """Load shifts from a CSV file with columns: shift_id, date, start_time, end_time."""
    shifts = []
    seen_ids: set = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"shift_id", "date", "start_time", "end_time"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Shifts CSV is missing required columns: {missing}")
        for row in reader:
            sid = row["shift_id"].strip()
            if not sid:
                continue
            if sid in seen_ids:
                raise ValueError(f"Duplicate shift_id in shifts file: '{sid}'")
            seen_ids.add(sid)
            shifts.append(
                Shift(
                    shift_id=sid,
                    date=row["date"].strip(),
                    start_time=row["start_time"].strip(),
                    end_time=row["end_time"].strip(),
                )
            )
    if not shifts:
        raise ValueError("Shifts file contains no data rows.")
    return shifts


def load_people(filepath: str) -> list:
    """
    Load people from a CSV file.

    Expected format (variable number of preference columns):
        name, pref_shift_1, pref_shift_2, ...
    All columns after 'name' are treated as ordered preferred shift IDs.
    Empty cells are skipped.
    """
    people = []
    seen_names: set = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("People file is empty.")
        if not header or not header[0].strip().lower().startswith("name"):
            raise ValueError("People CSV must have 'name' as the first column header.")
        for row in reader:
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            if name in seen_names:
                raise ValueError(f"Duplicate person name in people file: '{name}'")
            seen_names.add(name)
            preferences = [cell.strip() for cell in row[1:] if cell.strip()]
            people.append(Person(name=name, preferences=preferences))
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
        "target": g.get("target_shifts_per_person", 3),
        "min": g.get("min_shifts_per_person", 1),
        "max": g.get("max_shifts_per_person", 5),
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
                "target": override.get("target", defaults["target"]),
                "min": override.get("min", defaults["min"]),
                "max": override.get("max", defaults["max"]),
            }

    result: dict = {}
    for person in people:
        if person.name in per_person:
            result[person.name] = per_person[person.name]
        else:
            result[person.name] = dict(defaults)
    return result
