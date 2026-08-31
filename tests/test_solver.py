"""Baseline tests for scheduler.solver."""
import pytest

from scheduler.loader import Person, Shift
from scheduler.solver import InfeasibleError, solve_two_pass


def _shifts(n=2):
    return [
        Shift(shift_id=f"s{i}", date=f"2026-04-0{i}", start_time="08:00",
              end_time="16:00", points=1.0)
        for i in range(1, n + 1)
    ]


def test_feasible_solve_assigns_every_shift():
    shifts = _shifts(2)
    people = [
        Person(name="Alice", preferences=["s1"]),
        Person(name="Bob", preferences=["s2"]),
    ]
    constraints = {name: {"target": 1.0, "min": 1.0, "max": 1.0} for name in ("Alice", "Bob")}
    results, pass2 = solve_two_pass(shifts, people, constraints)
    assert len(results) == 2
    by_id = {r["shift_id"]: r for r in results}
    assert by_id["s1"]["person"] == "Alice"
    assert by_id["s2"]["person"] == "Bob"
    assert all(r["is_preferred"] for r in results)
    assert pass2 == []


def test_infeasible_capacity_raises():
    shifts = _shifts(2)  # 2 points of work
    people = [Person(name="Alice")]
    constraints = {"Alice": {"target": 1.0, "min": 0.0, "max": 1.0}}  # capacity 1 < 2
    with pytest.raises(InfeasibleError):
        solve_two_pass(shifts, people, constraints)


def test_two_pass_reassigns_non_preferred():
    shifts = _shifts(2)
    people = [
        Person(name="Alice", preferences=["s1"]),
        Person(name="Bob", preferences=[]),  # no preferences: pass-1 fill is non-preferred
    ]
    constraints = {name: {"target": 1.0, "min": 1.0, "max": 1.0} for name in ("Alice", "Bob")}
    results, pass2 = solve_two_pass(shifts, people, constraints, pass2_min=0.0, pass2_max=2.0)
    assert len(results) == 2
    assert {r["person"] for r in results} == {"Alice", "Bob"}
