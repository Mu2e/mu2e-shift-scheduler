"""Baseline tests for scheduler.loader behavior the new features depend on."""
import pytest

from scheduler.loader import (
    Person,
    _default_points,
    build_constraints,
    load_people,
    load_shifts,
    validate,
)
from tests.conftest import write_csv


def test_load_shifts_required_columns(tmp_path):
    path = write_csv(tmp_path / "bad.csv", ["shift_id", "date"], [["s1", "2026-04-01"]])
    with pytest.raises(ValueError, match="missing required columns"):
        load_shifts(str(path))


def test_load_shifts_duplicate_id(tmp_path):
    path = write_csv(
        tmp_path / "dup.csv",
        ["shift_id", "date", "start_time", "end_time"],
        [["s1", "2026-04-01", "08:00", "16:00"], ["s1", "2026-04-02", "08:00", "16:00"]],
    )
    with pytest.raises(ValueError, match="Duplicate shift_id"):
        load_shifts(str(path))


def test_load_shifts_points_column_and_defaults(shifts_csv):
    config = {"shift_points": {"default": 1.0, "night": 2.0,
                               "night_start": "20:00", "night_end": "08:00"}}
    shifts = {s.shift_id: s for s in load_shifts(str(shifts_csv), config)}
    assert shifts["s1"].points == 1.0   # explicit
    assert shifts["s3"].points == 2.0   # explicit
    assert shifts["s4"].points == 1.0   # empty cell -> day default (08:00 start)


def test_default_points_night_window_wraps_midnight():
    sp = {"default": 1.0, "night": 2.0, "night_start": "20:00", "night_end": "08:00"}
    assert _default_points("21:00", sp) == 2.0
    assert _default_points("03:00", sp) == 2.0
    assert _default_points("08:00", sp) == 1.0
    assert _default_points("12:00", sp) == 1.0


def test_load_people(people_csv):
    people = load_people(str(people_csv))
    assert [p.name for p in people] == ["Alice", "Bob"]
    assert people[0].institution == "Fermilab"
    assert people[0].preferences == ["s1", "s2"]
    assert people[1].preferences == ["s3"]


def test_validate_unknown_preference(shifts_csv, people_csv, tmp_path):
    shifts = load_shifts(str(shifts_csv))
    bad_people = write_csv(
        tmp_path / "bad_people.csv",
        ["name", "pref_1"],
        [["Carol", "no-such-shift"]],
    )
    people = load_people(str(bad_people))
    with pytest.raises(ValueError, match="unknown shift"):
        validate(shifts, people)


def test_build_constraints_priority():
    people = [Person(name="Alice"), Person(name="Bob")]
    config = {
        "global": {"target_points_per_person": 3.0,
                   "min_points_per_person": 1.0,
                   "max_points_per_person": 5.0},
        "overrides": [{"name": "Bob", "min": 0.0, "max": 2.0, "target": 1.0}],
    }
    constraints = build_constraints(people, config, {"target": 4.0})
    assert constraints["Alice"] == {"target": 4.0, "min": 1.0, "max": 5.0}
    assert constraints["Bob"] == {"target": 1.0, "min": 0.0, "max": 2.0}
