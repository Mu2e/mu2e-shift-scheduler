"""
Integer linear programming solver for shift scheduling using PuLP + CBC.

Objective (maximization):
    + preference_score   (weighted sum: first preference > second > ...)
    - alpha * deviation  (penalize deviation from per-person target shift count)

Hard constraints:
    - Each shift is assigned to exactly one person.
    - Each person's total shifts is within their [min, max] range.
"""
from pulp import (
    LpProblem,
    LpVariable,
    LpMaximize,
    LpStatus,
    lpSum,
    value,
    PULP_CBC_CMD,
)

from .loader import Shift, Person


class InfeasibleError(Exception):
    """Raised when no valid assignment exists under the given constraints."""


def solve(
    shifts: list,
    people: list,
    constraints: dict,
    alpha: float = 1.0,
) -> list:
    """
    Assign people to shifts using ILP.

    Args:
        shifts:      list of Shift objects
        people:      list of Person objects (with ordered preference lists)
        constraints: per-person dict { name: {"target", "min", "max"} }
        alpha:       weight of load-balancing penalty vs. preference satisfaction

    Returns:
        Sorted list of assignment dicts:
            { shift_id, date, start_time, end_time, person, is_preferred, pref_rank }

    Raises:
        InfeasibleError: if no feasible assignment exists.
        ValueError:      if inputs are empty.
    """
    if not shifts:
        raise ValueError("No shifts provided.")
    if not people:
        raise ValueError("No people provided.")

    # --- Build preference weight lookup ---
    # Weight = (number of preferences - rank), so first preference gets the highest weight.
    pref_weight: dict = {}
    for person in people:
        n = len(person.preferences)
        pref_weight[person.name] = {sid: (n - rank) for rank, sid in enumerate(person.preferences)}

    shift_ids = [s.shift_id for s in shifts]
    person_names = [p.name for p in people]
    S = len(shift_ids)
    P = len(person_names)

    # Quick capacity check before building the (potentially large) model
    total_min = sum(constraints[pn]["min"] for pn in person_names)
    total_max = sum(constraints[pn]["max"] for pn in person_names)
    if total_max < S:
        raise InfeasibleError(
            f"Not enough capacity: total max shifts ({total_max}) across {P} people "
            f"is less than the number of shifts to fill ({S}). "
            f"Increase max_shifts_per_person or add more people."
        )
    if total_min > S:
        raise InfeasibleError(
            f"Too many required shifts: total min shifts ({total_min}) across {P} people "
            f"exceeds the number of available shifts ({S}). "
            f"Decrease min_shifts_per_person."
        )

    # --- Build ILP model ---
    prob = LpProblem("shift_scheduling", LpMaximize)

    # x[i][j] = 1 iff person i is assigned to shift j  (using integer indices to avoid
    # PuLP variable name issues with hyphens/spaces in shift IDs and person names)
    x = {
        (i, j): LpVariable(f"x_{i}_{j}", cat="Binary")
        for i in range(P)
        for j in range(S)
    }

    # d[i] >= |actual_shifts_i - target_i|  (linearized absolute deviation)
    d = {i: LpVariable(f"d_{i}", lowBound=0) for i in range(P)}

    # --- Objective ---
    pref_score = lpSum(
        pref_weight[person_names[i]].get(shift_ids[j], 0) * x[i, j]
        for i in range(P)
        for j in range(S)
    )
    deviation_penalty = alpha * lpSum(d[i] for i in range(P))
    prob += pref_score - deviation_penalty, "objective"

    # --- Constraints ---
    # 1. Each shift assigned to exactly one person.
    for j in range(S):
        prob += lpSum(x[i, j] for i in range(P)) == 1, f"cover_j{j}"

    # 2. Per-person min/max and target-deviation tracking.
    for i in range(P):
        pn = person_names[i]
        c = constraints[pn]
        total_i = lpSum(x[i, j] for j in range(S))
        prob += total_i >= c["min"], f"min_i{i}"
        prob += total_i <= c["max"], f"max_i{i}"
        prob += d[i] >= total_i - c["target"], f"dpos_i{i}"
        prob += d[i] >= c["target"] - total_i, f"dneg_i{i}"

    # --- Solve ---
    prob.solve(PULP_CBC_CMD(msg=0))

    status_str = LpStatus[prob.status]
    if prob.status != 1:
        raise InfeasibleError(
            f"Solver could not find a feasible solution (status: {status_str}). "
            f"Check that your min/max/target constraints are achievable given the number "
            f"of shifts and people."
        )

    # --- Extract assignments ---
    shift_map = {s.shift_id: s for s in shifts}
    pref_lists = {p.name: p.preferences for p in people}
    results = []

    for j in range(S):
        sid = shift_ids[j]
        assigned_person = "UNASSIGNED"
        is_preferred = False
        pref_rank = None

        for i in range(P):
            v = value(x[i, j])
            if v is not None and v > 0.5:
                pn = person_names[i]
                assigned_person = pn
                prefs = pref_lists[pn]
                if sid in prefs:
                    is_preferred = True
                    pref_rank = prefs.index(sid) + 1
                break

        s = shift_map[sid]
        results.append(
            {
                "shift_id": sid,
                "date": s.date,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "person": assigned_person,
                "is_preferred": is_preferred,
                "pref_rank": pref_rank,
            }
        )

    results.sort(key=lambda r: (r["date"], r["start_time"]))
    return results


def solve_two_pass(
    shifts: list,
    people: list,
    constraints: dict,
    alpha: float = 1.0,
    pass2_min: int = 0,
    pass2_max: int = 1000,
) -> tuple:
    """
    Two-pass solver: first pass uses the given constraints; second pass
    re-solves only the shifts not filled by a preferred person, using
    per-person constraints of pass2_min / pass2_max.

    Returns:
        (merged, pass2_results) where:
            merged        – full assignment list (all shifts), sorted by (date, start_time)
            pass2_results – assignments from the second solve only (empty list if not needed)
    """
    # --- Pass 1 ---
    pass1 = solve(shifts, people, constraints, alpha=alpha)

    # Identify shifts not filled by a preferred person
    unfilled = [r for r in pass1 if not r["is_preferred"]]
    if not unfilled:
        return pass1, []

    # Build the subset of Shift objects for pass 2
    unfilled_ids = {r["shift_id"] for r in unfilled}
    shift_map = {s.shift_id: s for s in shifts}
    subset_shifts = [shift_map[sid] for sid in unfilled_ids]

    # Pass-2 constraints: configurable min/max, target unchanged
    relaxed = {
        pn: {"min": pass2_min, "max": pass2_max, "target": constraints[pn]["target"]}
        for pn in constraints
    }

    # --- Pass 2 ---
    pass2 = solve(subset_shifts, people, relaxed, alpha=alpha)

    # Merge: replace pass-1 results for unfilled shifts with pass-2 results
    pass2_map = {r["shift_id"]: r for r in pass2}
    merged = []
    for r in pass1:
        if r["shift_id"] in pass2_map:
            merged.append(pass2_map[r["shift_id"]])
        else:
            merged.append(r)

    merged.sort(key=lambda r: (r["date"], r["start_time"]))
    return merged, pass2
