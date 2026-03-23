#!/usr/bin/env python3
"""
Generate the Mu2e 26-week block-shift dataset.

Each "shift" represents a person covering one shift type for an entire
multi-day block:

  Weekday block  Mon–Thu  :  day   08:00–16:00
                              evening  16:00–23:59
                              night    00:00–08:00

  Weekend block  Fri–Sun  :  day   08:00–16:00
                              evening  16:00–23:59
                              night    00:00–08:00

Per week : 2 blocks × 3 types = 6 shift blocks
26 weeks : 26 × 6            = 156 shift blocks

Outputs
-------
  sample_data/shifts_blocks.csv   — 156 shift blocks
  sample_data/people_blocks.csv   — 96 people, each with ≥6 ordered preferences
"""
import csv
import random
from collections import defaultdict
from datetime import date, timedelta

random.seed(13)   # reproducible

# ---------------------------------------------------------------------------
# Schedule parameters
# ---------------------------------------------------------------------------
START_DATE = date(2026, 4, 6)    # must be a Monday
NUM_WEEKS  = 26

SLOT_DEFS = [
    ("day",     "08:00", "16:00"),
    ("evening", "16:00", "23:59"),
    ("night",   "00:00", "08:00"),
]

# ---------------------------------------------------------------------------
# Build shift blocks
# ---------------------------------------------------------------------------
# shift_id convention:
#   W{ww}-day / W{ww}-eve / W{ww}-ngt   — weekday (Mon–Thu) blocks
#   E{ww}-day / E{ww}-eve / E{ww}-ngt   — weekend (Fri–Sun) blocks
# where {ww} is the 2-digit week number within the 26-week period.

SLOT_CODE = {"day": "day", "evening": "eve", "night": "ngt"}

shifts = []

# Index by (block_type, slot_label) for preference sampling
block_slot_index: dict = defaultdict(list)   # ("weekday"|"weekend", slot) -> [shift_ids]

for week in range(NUM_WEEKS):
    week_num   = week + 1
    monday     = START_DATE + timedelta(weeks=week)
    friday     = monday + timedelta(days=4)
    thursday   = monday + timedelta(days=3)
    sunday     = monday + timedelta(days=6)

    for slot_label, start_time, end_time in SLOT_DEFS:
        code = SLOT_CODE[slot_label]

        # --- Weekday block (Mon–Thu) ---
        w_id = f"W{week_num:02d}-{code}"
        shifts.append({
            "shift_id":   w_id,
            "date":       monday.isoformat(),     # block start date (required by loader)
            "date_end":   thursday.isoformat(),   # informational
            "start_time": start_time,
            "end_time":   end_time,
            "shift_type": slot_label,
            "block_type": "weekday",
        })
        block_slot_index[("weekday", slot_label)].append(w_id)

        # --- Weekend block (Fri–Sun) ---
        e_id = f"E{week_num:02d}-{code}"
        shifts.append({
            "shift_id":   e_id,
            "date":       friday.isoformat(),     # block start date
            "date_end":   sunday.isoformat(),     # informational
            "start_time": start_time,
            "end_time":   end_time,
            "shift_type": slot_label,
            "block_type": "weekend",
        })
        block_slot_index[("weekend", slot_label)].append(e_id)

assert len(shifts) == NUM_WEEKS * 6, f"Expected {NUM_WEEKS*6} shifts, got {len(shifts)}"

all_shift_ids = [s["shift_id"] for s in shifts]

# ---------------------------------------------------------------------------
# People (96)
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
#
# Each profile maps to one or two (block_type, slot_label) pools.
# primary pools  → ordered first in the preference list (most preferred)
# secondary pools → appended after, in case more entries are needed
#
# num_prefs is (min, max) for how many shifts to include in the list.
# The hard minimum of 6 is enforced in sample_preferences().
# ---------------------------------------------------------------------------
PROFILES = [
    # 0 — weekday day
    {"primary":   [("weekday", "day")],
     "secondary": [("weekday", "evening")],
     "num_prefs": (8, 14)},

    # 1 — weekday evening
    {"primary":   [("weekday", "evening")],
     "secondary": [("weekday", "day")],
     "num_prefs": (8, 14)},

    # 2 — weekday night
    {"primary":   [("weekday", "night")],
     "secondary": [("weekend", "night")],
     "num_prefs": (6, 12)},

    # 3 — weekend day
    {"primary":   [("weekend", "day")],
     "secondary": [("weekend", "evening")],
     "num_prefs": (8, 14)},

    # 4 — weekend evening
    {"primary":   [("weekend", "evening")],
     "secondary": [("weekend", "day")],
     "num_prefs": (8, 14)},

    # 5 — weekend night
    {"primary":   [("weekend", "night")],
     "secondary": [("weekday", "night")],
     "num_prefs": (6, 12)},

    # 6 — flexible weekday (day or evening, Mon–Thu)
    {"primary":   [("weekday", "day"), ("weekday", "evening")],
     "secondary": [("weekday", "night")],
     "num_prefs": (10, 16)},

    # 7 — flexible weekend (day or evening, Fri–Sun)
    {"primary":   [("weekend", "day"), ("weekend", "evening")],
     "secondary": [("weekend", "night")],
     "num_prefs": (10, 16)},
]


def sample_preferences(profile: dict, min_prefs: int = 6) -> list:
    """
    Build an ordered preference list for one person.

    Shifts from the primary pools appear first (highest preference rank),
    followed by secondary-pool shifts.  The list is guaranteed to contain
    at least min_prefs entries.
    """
    # Shuffle each pool independently to avoid always picking early weeks
    def shuffled_pool(key: tuple) -> list:
        p = list(block_slot_index[key])
        random.shuffle(p)
        return p

    primary_ids: list = []
    for key in profile["primary"]:
        primary_ids.extend(shuffled_pool(key))

    secondary_ids: list = []
    for key in profile["secondary"]:
        secondary_ids.extend(shuffled_pool(key))

    # Merge, deduplicating while preserving primary-first order
    seen: set = set()
    ordered: list = []
    for sid in primary_ids + secondary_ids:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)

    lo, hi = profile["num_prefs"]
    n = random.randint(lo, hi)
    selected = ordered[:n]

    # Guarantee minimum: pad with random shifts not already chosen
    if len(selected) < min_prefs:
        fallback = [sid for sid in all_shift_ids if sid not in set(selected)]
        random.shuffle(fallback)
        selected.extend(fallback[: min_prefs - len(selected)])

    return selected


people = []
for i, name in enumerate(names):
    profile = PROFILES[i % len(PROFILES)]
    prefs   = sample_preferences(profile, min_prefs=6)
    people.append({"name": name, "preferences": prefs})

# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------
shifts_path = "sample_data/shifts_blocks.csv"
people_path = "sample_data/people_blocks.csv"

# Shifts: include all columns (loader only reads the four required ones;
# extra columns are preserved for human readability).
shift_fields = ["shift_id", "date", "date_end", "start_time", "end_time",
                "shift_type", "block_type"]
with open(shifts_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=shift_fields)
    writer.writeheader()
    writer.writerows(shifts)

# People: variable-width preference columns
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
pref_counts  = [len(p["preferences"]) for p in people]
total_prefs  = sum(pref_counts)
avg_per_shift = total_prefs / len(shifts)

print(f"Shift blocks : {len(shifts)}")
print(f"  Weekday (Mon–Thu) : {NUM_WEEKS * 3}  ({NUM_WEEKS} weeks × 3 slot types)")
print(f"  Weekend (Fri–Sun) : {NUM_WEEKS * 3}  ({NUM_WEEKS} weeks × 3 slot types)")
print()
print(f"People  : {len(people)}")
print(f"Prefs   : {total_prefs} total  "
      f"(min {min(pref_counts)}, avg {total_prefs/len(people):.1f}, max {max(pref_counts)})")
print(f"         (~{avg_per_shift:.1f} preferences per shift block on average)")
print()
avg_shifts = len(shifts) / len(people)
print(f"Avg blocks/person if all filled : {avg_shifts:.2f}")
print(f"Recommended config              : target=2, min=1, max=4")
print(f"Capacity check                  : "
      f"96×1={96} ≤ {len(shifts)} ≤ 96×4={96*4}  "
      f"{'OK' if 96 <= len(shifts) <= 96*4 else 'INFEASIBLE'}")
print()
print(f"Written : {shifts_path}")
print(f"Written : {people_path}")
