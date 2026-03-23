#!/usr/bin/env python3
"""
Generate a large sample dataset for the Mu2e Shift Scheduler.

Produces:
  sample_data/shifts_large.csv  — 1092 shifts (26 weeks × 7 days × 6 shifts/day)
  sample_data/people_large.csv  — 200 people with realistic shift preferences
"""
import argparse
import csv
import random
from collections import defaultdict
from datetime import date, timedelta

parser = argparse.ArgumentParser(description="Generate sample shift/people CSV files.")
parser.add_argument(
    "--seed",
    type=int,
    default=42,
    metavar="N",
    help="Random seed for reproducibility (default: 42).",
)
args = parser.parse_args()

random.seed(args.seed)

# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------
START_DATE = date(2026, 4, 6)   # Monday
NUM_WEEKS  = 26
SHIFT_TIMES = [
    ("00:00", "04:00"),   # slot 0 — graveyard
    ("04:00", "08:00"),   # slot 1 — early morning
    ("08:00", "12:00"),   # slot 2 — morning
    ("12:00", "16:00"),   # slot 3 — afternoon
    ("16:00", "20:00"),   # slot 4 — early evening
    ("20:00", "00:00"),   # slot 5 — late evening
]

shifts = []
for day_idx in range(NUM_WEEKS * 7):
    d = START_DATE + timedelta(days=day_idx)
    for slot_idx, (start, end) in enumerate(SHIFT_TIMES):
        shift_num = day_idx * 6 + slot_idx + 1
        shifts.append({
            "shift_id":   f"shift-{shift_num:04d}",
            "date":       d.isoformat(),
            "start_time": start,
            "end_time":   end,
            "dow":        d.weekday(),   # 0 = Mon … 6 = Sun
            "slot":       slot_idx,
        })

# Index shifts for efficient preference sampling
slot_dow_index = defaultdict(list)
for s in shifts:
    slot_dow_index[(s["slot"], s["dow"])].append(s["shift_id"])

# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Hank",
    "Iris", "Jack", "Karen", "Liam", "Mia", "Noah", "Olivia", "Peter",
    "Quinn", "Rachel", "Sam", "Tara", "Uma", "Victor", "Wendy", "Xander",
    "Yara", "Zoe", "Aaron", "Beth", "Chris", "Diana", "Ethan", "Fiona",
    "George", "Hannah", "Ivan", "Julia", "Kevin", "Laura", "Mike", "Nora",
    "Oscar", "Paula", "Quincy", "Rose", "Steve", "Tammy", "Ulric", "Vera",
    "Walter", "Xena", "Yusuf", "Zara", "Alex", "Bella", "Cameron", "Dani",
    "Eli", "Faith", "Gabe", "Harper", "Ian", "Jade", "Kyle", "Luna",
    "Marcus", "Nina", "Omar", "Penny", "Rex", "Sara", "Theo", "Uri",
    "Val", "Wren", "Xin", "Yas", "Zack", "Abby", "Ben", "Cleo",
    "Dan", "Emma", "Fred", "Gia", "Hugo", "Ida", "Jon", "Kate",
    "Leo", "Mae", "Neil", "Ola", "Pat", "Remy", "Sky", "Tim",
    "Una", "Vince", "Wade", "Xiu", "Yuki", "Zed", "Andy", "Bri",
    "Cole", "Dee", "Evan", "Fern", "Gill", "Hal", "Ira", "Jen",
    "Ken", "Lin", "Mo", "Nan", "Otis", "Pam", "Raj", "Sue",
    "Tod", "Ula", "Van", "Win", "Xi", "Yul", "Zeb", "Abe",
    "Bea", "Cal", "Dot", "Ed", "Fay", "Gil", "Hoa", "Ike",
]
LAST_NAMES = [
    "Smith", "Jones", "Brown", "Davis", "Miller", "Wilson", "Moore", "Taylor",
    "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin", "Garcia",
    "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis", "Lee", "Walker",
    "Hall", "Allen", "Young", "Hernandez", "King", "Wright", "Lopez", "Hill",
    "Scott", "Green", "Adams", "Baker", "Gonzalez", "Nelson", "Carter",
    "Mitchell", "Perez", "Roberts", "Turner", "Phillips", "Campbell", "Parker",
    "Evans", "Edwards", "Collins", "Stewart", "Sanchez", "Morris", "Rogers",
]

# Build 200 unique names cycling through first × last name combinations
names = []
seen: set = set()
for ln in LAST_NAMES:
    for fn in FIRST_NAMES:
        candidate = f"{fn} {ln}"
        if candidate not in seen:
            seen.add(candidate)
            names.append(candidate)
        if len(names) == 200:
            break
    if len(names) == 200:
        break

assert len(names) == 200, f"Only generated {len(names)} unique names; add more first/last names."

# ---------------------------------------------------------------------------
# Worker "profiles" — each person is assigned one profile that drives which
# shifts they prefer.  Six archetypes cover typical scheduling populations.
# ---------------------------------------------------------------------------
# Each profile specifies:
#   preferred_slots : slot indices (0-5) this worker favours
#   preferred_dows  : day-of-week indices (0=Mon … 6=Sun) this worker favours
#   num_prefs       : (min, max) range for how many preferences to list
PROFILES = [
    # 0 — day shift, weekdays only
    {"slots": [2, 3],       "dows": list(range(5)),  "num_prefs": (20, 30)},
    # 1 — evening, any day
    {"slots": [4, 5],       "dows": list(range(7)),  "num_prefs": (20, 28)},
    # 2 — night owl (graveyard + late evening)
    {"slots": [0, 5],       "dows": list(range(7)),  "num_prefs": (20, 25)},
    # 3 — flexible day (morning through early evening)
    {"slots": [2, 3, 4],    "dows": list(range(7)),  "num_prefs": (20, 32)},
    # 4 — weekend warrior
    {"slots": [2, 3, 4],    "dows": [5, 6],          "num_prefs": (20, 20)},
    # 5 — early bird (04:00–12:00 window)
    {"slots": [1, 2],       "dows": list(range(5)),  "num_prefs": (20, 26)},
]

def build_preferences(profile: dict) -> list:
    """Sample a random ordered preference list for a given profile."""
    pool = []
    for slot in profile["slots"]:
        for dow in profile["dows"]:
            pool.extend(slot_dow_index[(slot, dow)])
    random.shuffle(pool)

    # Deduplicate while preserving order
    seen_ids: set = set()
    unique = []
    for sid in pool:
        if sid not in seen_ids:
            seen_ids.add(sid)
            unique.append(sid)

    lo, hi = profile["num_prefs"]
    n = random.randint(lo, hi)
    return unique[:min(n, len(unique))]


people = []
for i, name in enumerate(names):
    profile = PROFILES[i % len(PROFILES)]
    prefs = build_preferences(profile)
    people.append({"name": name, "preferences": prefs})

# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------
shifts_path = "sample_data/shifts_large.csv"
people_path = "sample_data/people_large.csv"

with open(shifts_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["shift_id", "date", "start_time", "end_time"])
    writer.writeheader()
    for s in shifts:
        writer.writerow({k: s[k] for k in ["shift_id", "date", "start_time", "end_time"]})

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
total_prefs = sum(len(p["preferences"]) for p in people)
print(f"Shifts : {len(shifts):>6}  ({NUM_WEEKS} weeks x 7 days x {len(SHIFT_TIMES)} shifts/day)")
print(f"People : {len(people):>6}")
print(f"Prefs  : {total_prefs:>6} total  (avg {total_prefs / len(people):.1f} per person)")
print()
print(f"Average shifts per person (if target=6): {len(shifts) / len(people):.2f}")
print(f"  -> feasible with min=4, max=8  "
      f"(capacity {200*4}–{200*8} vs {len(shifts)} shifts)")
print()
print(f"Written: {shifts_path}")
print(f"Written: {people_path}")
