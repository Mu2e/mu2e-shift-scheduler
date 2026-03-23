"""
Export scheduling results to CSV or JSON, and compute summary statistics.
"""
import csv
import io
import json


_FIELDNAMES = ["shift_id", "date", "start_time", "end_time", "person", "is_preferred", "pref_rank"]


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
    for r in results:
        pn = r["person"]
        if pn == "UNASSIGNED":
            continue
        if pn not in person_counts:
            person_counts[pn] = {"assigned": 0, "preferred": 0}
        person_counts[pn]["assigned"] += 1
        if r.get("is_preferred"):
            person_counts[pn]["preferred"] += 1

    # Build stats for every person in constraints (including those with 0 shifts)
    person_stats = []
    for pn, c in constraints.items():
        counts = person_counts.get(pn, {"assigned": 0, "preferred": 0})
        assigned = counts["assigned"]
        target = c.get("target", 0)
        person_stats.append(
            {
                "name": pn,
                "assigned": assigned,
                "preferred": counts["preferred"],
                "target": target,
                "min": c.get("min", 0),
                "max": c.get("max", 0),
                "deviation": assigned - target,
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
