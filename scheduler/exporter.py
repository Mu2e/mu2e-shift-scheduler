"""
Export scheduling results to CSV or JSON, and compute summary statistics.
"""
import csv
import io
import json
from collections import defaultdict


_FIELDNAMES = ["shift_id", "date", "start_time", "end_time", "points", "person", "institution", "is_preferred", "pref_rank"]


def to_csv(results: list, path: str) -> None:
    """Write assignment results to a CSV file."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


def to_json(results: list, path: str) -> None:
    """Write assignment results to a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)


def as_csv_string(results: list) -> str:
    """Return assignment results as a CSV-formatted string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(results)
    return buf.getvalue()


def as_json_string(results: list) -> str:
    """Return assignment results as a JSON-formatted string."""
    return json.dumps(results, indent=2, default=str)


def compute_stats(results: list, constraints: dict) -> dict:
    """
    Compute per-person assignment stats and overall schedule summary.

    Args:
        results: list of assignment dicts from solver.solve()
        constraints: per-person constraint dict from loader.build_constraints()

    Returns:
        {
            "person_stats": [ {name, assigned, preferred, target, min, max, deviation}, ... ],
            "total_shifts": int,
            "filled_shifts": int,
            "unfilled_shifts": int,
            "preferred_assignments": int,
            "preference_pct": float,
        }
    """
    person_counts: dict = {}
    person_inst: dict = {}
    for r in results:
        pn = r["person"]
        if pn == "UNASSIGNED":
            continue
        if pn not in person_counts:
            person_counts[pn] = {"assigned": 0, "preferred": 0, "points": 0.0}
        person_counts[pn]["assigned"] += 1
        person_counts[pn]["points"] += r.get("points", 1.0)
        if r.get("is_preferred"):
            person_counts[pn]["preferred"] += 1
        if pn not in person_inst:
            person_inst[pn] = r.get("institution", "")

    # Build stats for every person in constraints (including those with 0 shifts)
    person_stats = []
    for pn, c in constraints.items():
        counts = person_counts.get(pn, {"assigned": 0, "preferred": 0, "points": 0.0})
        assigned = counts["assigned"]
        points_assigned = counts["points"]
        target = c.get("target", 0)
        person_stats.append(
            {
                "name": pn,
                "institution": person_inst.get(pn, ""),
                "assigned": assigned,
                "points_assigned": round(points_assigned, 4),
                "preferred": counts["preferred"],
                "target": target,
                "min": c.get("min", 0),
                "max": c.get("max", 0),
                "deviation": round(points_assigned - target, 4),
            }
        )
    person_stats.sort(key=lambda s: s["name"])

    total = len(results)
    filled = sum(1 for r in results if r["person"] != "UNASSIGNED")
    preferred = sum(1 for r in results if r.get("is_preferred"))
    pref_pct = round(preferred / filled * 100, 1) if filled > 0 else 0.0

    return {
        "person_stats": person_stats,
        "total_shifts": total,
        "filled_shifts": filled,
        "unfilled_shifts": total - filled,
        "preferred_assignments": preferred,
        "preference_pct": pref_pct,
    }


def compute_institution_stats(results: list) -> list:
    """
    Compute per-institution shift assignment stats.

    Returns a list of dicts sorted by institution name:
        { institution, n_people, shifts, points }
    """
    buckets: dict = defaultdict(lambda: {"shifts": 0, "points": 0.0, "people": set()})
    for r in results:
        if r["person"] == "UNASSIGNED":
            continue
        inst = r.get("institution", "").strip() or "Unknown"
        buckets[inst]["shifts"] += 1
        buckets[inst]["points"] += r.get("points", 1.0)
        buckets[inst]["people"].add(r["person"])

    return [
        {
            "institution": inst,
            "n_people": len(s["people"]),
            "shifts": s["shifts"],
            "points": round(s["points"], 4),
        }
        for inst, s in sorted(buckets.items())
    ]
