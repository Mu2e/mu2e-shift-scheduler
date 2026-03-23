#!/usr/bin/env python3
"""
Convert a preferences JSON file to the people CSV format expected by the scheduler.

The JSON file contains an array of submissions:
    [
      {
        "name": "Alice",
        "submitted_at": "2026-03-22T10:30:00+00:00",
        "preferences": ["shift-0001", "shift-0004", ...]
      },
      ...
    ]

When a person has submitted more than once, only their most recent submission is used.

Output CSV format:
    name,pref_1,pref_2,...

Usage:
    python json_to_people_csv.py preferences.json people.csv
    python json_to_people_csv.py preferences.json          # prints to stdout
    python json_to_people_csv.py --help
"""
import argparse
import csv
import json
import sys
from pathlib import Path


def load_preferences(json_path: str) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def deduplicate(submissions: list[dict]) -> list[dict]:
    """Keep only the most recent submission per person (case-insensitive name match)."""
    latest: dict[str, dict] = {}
    for sub in submissions:
        key = sub["name"].strip().lower()
        existing = latest.get(key)
        if existing is None or sub["submitted_at"] > existing["submitted_at"]:
            latest[key] = sub
    # Return in original insertion order (by first appearance)
    seen = {}
    for sub in submissions:
        key = sub["name"].strip().lower()
        if key not in seen:
            seen[key] = latest[key]
    return list(seen.values())


def to_csv_rows(submissions: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Return (header, rows) ready for csv.writer."""
    max_prefs = max((len(s["preferences"]) for s in submissions), default=0)
    header = ["name"] + [f"pref_{i+1}" for i in range(max_prefs)]
    rows = []
    for sub in submissions:
        row = [sub["name"].strip()] + sub["preferences"]
        rows.append(row)
    return header, rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert preferences JSON to people CSV for the Mu2e scheduler.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("json_file", help="Path to the preferences JSON file.")
    parser.add_argument(
        "output_csv",
        nargs="?",
        help="Output CSV file path. Omit to print to stdout.",
    )
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Include all submissions instead of only the most recent per person.",
    )
    args = parser.parse_args()

    submissions = load_preferences(args.json_file)
    if not submissions:
        print("No submissions found in the JSON file.", file=sys.stderr)
        sys.exit(1)

    if not args.keep_duplicates:
        submissions = deduplicate(submissions)

    header, rows = to_csv_rows(submissions)

    if args.output_csv:
        with open(args.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        print(f"Wrote {len(rows)} people to {args.output_csv}")
    else:
        writer = csv.writer(sys.stdout)
        writer.writerow(header)
        writer.writerows(rows)


if __name__ == "__main__":
    main()
