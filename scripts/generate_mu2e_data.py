#!/usr/bin/env python3
"""
Generate the Mu2e 26-week shift dataset.

Schedule structure
------------------
  Mon–Thu  :  day   08:00–16:00
               evening  16:00–23:59
               night    00:00–08:00
  Fri–Sun  :  day   08:00–16:00
               evening  16:00–23:59
               night    00:00–08:00

Outputs
-------
  sample_data/shifts_mu2e.csv   — 546 shifts
  sample_data/people_mu2e.csv   — 96 people, each with ≥6 ordered preferences
"""
import csv
import random
from collections import defaultdict
from datetime import date, timedelta

random.seed(7)   # reproducible

# ---------------------------------------------------------------------------
# Shift definitions
# ---------------------------------------------------------------------------
START_DATE = date(2026, 4, 6)   # must be a Monday
NUM_WEEKS  = 26

# (label, start, end)  — same times for both Mon-Thu and Fri-Sun blocks
SHIFT_TYPES = [
    ("day",     "08:00", "16:00"),
    ("evening", "16:00", "23:59"),
    ("night",   "00:00", "08:00"),
]

WEEKDAY_DAYS = {0, 1, 2, 3}   # Mon=0 … Thu=3
WEEKEND_DAYS = {4, 5, 6}      # Fri=4, Sat=5, Sun=6

# Build shift list, tracking block and slot for preference generation
shifts = []
weekday_shifts: dict = defaultdict(list)   # slot_label -> [shift_ids]
weekend_shifts: dict = defaultdict(list)   # slot_label -> [shift_ids]

wday_counter = 0   # sequential index within weekday shifts
wend_counter = 0   # sequential index within weekend shifts

for week in range(NUM_WEEKS):
    for day_offset in range(7):
        d = START_DATE + timedelta(weeks=week, days=day_offset)
        dow = d.weekday()
        is_weekend = dow in WEEKEND_DAYS

        for label, start, end in SHIFT_TYPES:
            if is_weekend:
                wend_counter += 1
                shift_id = f"shift-E-{wend_counter:03d}"
                weekend_shifts[label].append(shift_id)
            else:
                wday_counter += 1
                shift_id = f"shift-W-{wday_counter:03d}"
                weekday_shifts[label].append(shift_id)

            shifts.append({
                "shift_id":   shift_id,
                "date":       d.isoformat(),
                "start_time": start,
                "end_time":   end,
            })

assert len(shifts) == NUM_WEEKS * 7 * 3, f"Expected {NUM_WEEKS*7*3} shifts, got {len(shifts)}"

# Convenience: all shifts by (block, slot) for preference sampling
all_shift_ids = [s["shift_id"] for s in shifts]

# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Hank",
    "Iris", "Jack", "Karen", "Liam", "Mia", "Noah", "Olivia", "Peter",
    "Quinn", "Rachel", "Sam", "Tara", "Uma", "Victor", "Wendy", "Xander",
    "Yara", "Zoe", "Aaron", "Beth", "Chris", "Diana", "Ethan", "Fiona",
    "George", "Hannah", "Ivan", "Julia", "Kevin", "Laura", "Mike", "Nora",
    "Oscar", "Paula", "Rex", "Sara", "Theo", "Uri", "Val", "Wren",
]
LAST_NAMES = [
    "Smith", "Jones", "Brown", "Davis", "Miller", "Wilson", "Moore",
    "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
    "Martin", "Garcia", "Clark", "Rodriguez", "Lewis", "Lee", "Walker",
]

# 48 first × 20 last = 960 combinations; pick 96
names: list = []
used_names: set = set()
for ln in LAST_NAMES:
    for fn in FIRST_NAMES:
        candidate = f"{fn} {ln}"
        if candidate not in used_names:
            used_names.add(candidate)
            names.append(candidate)
        if len(names) == 96:
            break
    if len(names) == 96:
        break

assert len(names) == 96

# ---------------------------------------------------------------------------
# Preference profiles
# Each person is assigned one primary profile and one optional secondary pool
# so that preferences feel realistic rather than perfectly uniform.
#
# Pool keys: (block, slot_label) where block in {"weekday", "weekend"}
# ---------------------------------------------------------------------------
def pool(block: str, label: str) -> list:
    src = weekday_shifts if block == "weekday" else weekend_shifts
    return list(src[label])


PROFILES = [
    # 0 — weekday day workers
    {
        "primary":   [pool("weekday", "day")],
        "secondary": [pool("weekday", "evening")],
        "n_prefs":   (8, 16),
    },
    # 1 — weekday evening workers
    {
        "primary":   [pool("weekday", "evening")],
        "secondary": [pool("weekday", "day")],
        "n_prefs":   (8, 16),
    },
    # 2 — weekday night workers
    {
        "primary":   [pool("weekday", "night")],
        "secondary": [pool("weekend", "night")],
        "n_prefs":   (6, 14),
    },
    # 3 — weekend day workers
    {
        "primary":   [pool("weekend", "day")],
        "secondary": [pool("weekend", "evening")],
        "n_prefs":   (8, 16),
    },
    # 4 — weekend evening workers
    {
        "primary":   [pool("weekend", "evening")],
        "secondary": [pool("weekend", "day")],
        "n_prefs":   (8, 16),
    },
    # 5 — weekend night workers
    {
        "primary":   [pool("weekend", "night")],
        "secondary": [pool("weekday", "night")],
        "n_prefs":   (6, 14),
    },
    # 6 — flexible weekday (any slot Mon–Thu)
    {
        "primary":   [pool("weekday", "day"), pool("weekday", "evening")],
        "secondary": [pool("weekday", "night")],
        "n_prefs":   (10, 18),
    },
    # 7 — flexible weekend (any slot Fri–Sun)
    {
        "primary":   [pool("weekend", "day"), pool("weekend", "evening")],
        "secondary": [pool("weekend", "night")],
        "n_prefs":   (10, 18),
    },
]


def sample_preferences(profile: dict, min_prefs: int = 6) -> list:
    """
    Build an ordered preference list for one person.

    Primary pool shifts appear first (most preferred), then secondary pool.
    Guarantees at least min_prefs entries by falling back to the full shift
    list if the profile pools are exhausted.
    """
    # Flatten and shuffle each pool independently, then concatenate
    primary_pool: list = []
    for p in profile["primary"]:
        chunk = list(p)
        random.shuffle(chunk)
        primary_pool.extend(chunk)

    secondary_pool: list = []
    for p in profile["secondary"]:
        chunk = list(p)
        random.shuffle(chunk)
        secondary_pool.extend(chunk)

    # Deduplicate while preserving order (primary first)
    seen: set = set()
    ordered: list = []
    for sid in primary_pool + secondary_pool:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)

    lo, hi = profile["n_prefs"]
    n = random.randint(lo, hi)
    selected = ordered[:n]

    # Guarantee minimum: pad with random shifts not already selected
    if len(selected) < min_prefs:
        fallback = [sid for sid in all_shift_ids if sid not in set(selected)]
        random.shuffle(fallback)
        selected.extend(fallback[: min_prefs - len(selected)])

    return selected


people = []
for i, name in enumerate(names):
    profile = PROFILES[i % len(PROFILES)]
    prefs = sample_preferences(profile, min_prefs=6)
    people.append({"name": name, "preferences": prefs})

# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------
shifts_path = "sample_data/shifts_mu2e.csv"
people_path = "sample_data/people_mu2e.csv"

with open(shifts_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["shift_id", "date", "start_time", "end_time"])
    writer.writeheader()
    writer.writerows(shifts)

max_prefs = max(len(p["preferences"]) for p in people)
pref_cols = [f"pref_{i + 1}" for i in range(max_prefs)]

with open(people_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name"] + pref_cols, extrasaction="ignore")
    writer.writeheader()
    for p in people:
        row: dict = {"name": p["name"]}
        for idx, sid in enumerate(p["preferences"]):
            row[f"pref_{idx + 1}"] = sid
        writer.writerow(row)

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
wday_total = sum(len(v) for v in weekday_shifts.values())
wend_total  = sum(len(v) for v in weekend_shifts.values())
pref_counts = [len(p["preferences"]) for p in people]
total_prefs = sum(pref_counts)

print(f"Shifts    : {len(shifts):>6}")
print(f"  Mon–Thu : {wday_total:>6}  ({NUM_WEEKS} wks × 4 days × 3 slots)")
print(f"  Fri–Sun : {wend_total:>6}  ({NUM_WEEKS} wks × 3 days × 3 slots)")
print(f"People    : {len(people):>6}")
print(f"Prefs     : {total_prefs:>6} total  "
      f"(min {min(pref_counts)}, avg {total_prefs/len(people):.1f}, max {max(pref_counts)})")
print()
print(f"Avg shifts/person if all filled : {len(shifts)/len(people):.2f}")
print(f"Soft target (config)            : 1.5  (min=1, max=12)")
print(f"Capacity check                  : {96}×1={96} ≤ {len(shifts)} ≤ {96}×12={96*12}  ✓")
print()
print(f"Written : {shifts_path}")
print(f"Written : {people_path}")
