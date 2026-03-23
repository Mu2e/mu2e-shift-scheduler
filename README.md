# Mu2e Shift Scheduler

A shift scheduling tool for the Mu2e experiment at Fermilab. It takes a set of
defined shifts (arbitrary time blocks) and a list of people with ranked
preferences, then finds an optimal assignment using integer linear programming.
Constraints such as per-person shift-point targets, minimums, maximums, and a
fairness/preference tradeoff weight are all configurable.

The tool includes a web interface for participants to submit preferences, and
for coordinators to run and review the schedule.  All data is stored in
human-readable CSV and JSON formats.

---

## Features

- **ILP solver** — uses [PuLP](https://coin-or.github.io/pulp/) + the bundled
  CBC solver; no external solver installation required.
- **Shift points** — each shift carries a point value (default: 2.0 for night
  shifts, 1.0 for all others). All constraints and the objective function are
  expressed in shift-points, not raw shift counts.
- **Preference ranking** — people list shifts in order of preference; the
  most-preferred shift scores highest in the objective.
- **Configurable constraints** — per-person target, minimum, and maximum
  shift-points set via a YAML config file and/or the web UI form.
- **Per-person overrides** — individual constraints can be set in `config.yaml`
  under `overrides:`.
- **Fairness vs. preference tradeoff** — an `alpha` parameter controls how
  heavily the solver penalises deviation from each person's target points.
- **Two-pass solver** — a second pass re-optimises shifts not filled by a
  preferred person, with independently configurable bounds.
- **Institutional affiliation** — people can be tagged with an institution
  (e.g. Fermilab, Argonne, University of Minnesota). Results include a
  per-institution breakdown of shift points earned.
- **Web interface** — browser-based preference submission (with list and
  calendar views), schedule configuration, results viewing, and file download.
- **CLI interface** — `python3 cli.py solve` for scripted/batch use.
- **CSV and JSON output** — assignments exported in either format, including
  shift points and institution.

---

## Input File Formats

### shifts.csv

One row per shift.

| Column | Required | Description |
|---|---|---|
| `shift_id` | yes | Unique identifier, e.g. `shift-0001` |
| `date` | yes | ISO 8601 date, e.g. `2026-04-06` |
| `start_time` | yes | 24-hour time, e.g. `08:00` |
| `end_time` | yes | 24-hour time, e.g. `16:00` |
| `points` | no | Decimal point value for this shift. If omitted, defaults to `2.0` for night shifts and `1.0` otherwise (thresholds configurable in `config.yaml`). |

```csv
shift_id,date,start_time,end_time,points
shift-0001,2026-04-06,08:00,16:00,1.0
shift-0002,2026-04-06,16:00,20:00,1.0
shift-0003,2026-04-06,20:00,00:00,2.0
```

### people.csv

One row per person.

| Column | Required | Description |
|---|---|---|
| `name` | yes | Full name; must be first column |
| `institution` | no | Institutional affiliation, e.g. `Fermilab` |
| `pref_1`, `pref_2`, … | no | Preferred shift IDs in order of preference (most preferred first); empty cells are ignored |

```csv
name,institution,pref_1,pref_2,pref_3
Alice Smith,Fermilab,shift-0001,shift-0007,shift-0013
Bob Jones,Argonne,shift-0002,shift-0008
Carol Lee,University of Minnesota,shift-0001,shift-0003,shift-0007
```

People may list any number of preferences or none at all.

---

## Scheduling Model

### Decision variables

`x[i][j]` — binary: 1 if person *i* is assigned to shift *j*.

### Objective (maximised)

```
  preference_score  −  alpha × load_deviation
```

- **preference\_score** — rank-weighted sum of preference matches
  (first preference scores *N*, second *N−1*, etc., where *N* is the number
  of preferences the person listed).
- **load\_deviation** — sum of |points\_i − target\_i| for every person,
  linearised with auxiliary variables. Quantities are in shift-points.
- **alpha** — scalar controlling the preference/fairness tradeoff.

### Hard constraints

1. Each shift is assigned to **exactly one** person.
2. Each person's total **shift-points** is within their `[min, max]` range.

### Soft constraint

Each person's total shift-points should be as close as possible to `target`
(penalised in the objective, not enforced as a hard bound).

### Two-pass solve

After the first pass, any shift not filled by a preferred person is
re-optimised in a second pass with independently configurable `pass2_min` and
`pass2_max` point bounds. This improves preference satisfaction for the
majority of shifts while still filling the remainder.

---

## Configuration (`config.yaml`)

```yaml
global:
  target_points_per_person: 2.0   # soft target (in shift-points)
  min_points_per_person: 1.0      # hard lower bound
  max_points_per_person: 4.0      # hard upper bound
  pass2_min_points_per_person: 0.0
  pass2_max_points_per_person: 2.0

# Shift point defaults (used when the shifts CSV has no "points" column).
shift_points:
  default: 1.0          # normal shift
  night: 2.0            # night shift
  night_start: "20:00"  # start of night window (inclusive)
  night_end: "08:00"    # end of night window (exclusive); wraps midnight

# Tradeoff between preference satisfaction and equitable load distribution.
# Higher alpha → more equitable; lower alpha → more preference-driven.
alpha: 1.0

# Per-person overrides (take precedence over global defaults).
overrides:
  - name: "Alice Smith"
    min: 2.0
    max: 4.0
    target: 3.0
```

All global constraint values can also be set directly in the web UI form.
The old `_shifts_per_person` key names are still accepted as fallbacks.

---

## Web Interface

### Preference submission (`/preferences`)

- Participants enter their name and institution, then select and rank their
  preferred shifts.
- Shifts can be browsed as a **list** or as a **weekly calendar** (toggle
  in the UI); in calendar view, clicking a shift adds it to preferences.
- Submissions are stored in `preferences.json` and can be reviewed at
  `/preferences/current`.
- A utility script (`json_to_people_csv.py`) converts the JSON to a
  `people.csv` ready for the solver.

### Schedule configuration and results (`/schedule`)

- Upload `shifts.csv` and `people.csv`, set point constraints, and run the
  solver.
- Results pages:
  - **Assignments** — full shift-by-shift table with person, institution,
    points, and preference status.
  - **Per-person summary** — shifts assigned, points earned, target/min/max,
    deviation, and preference rate.
  - **By Institution** — total shift-points and shift counts grouped by
    institution, with a proportional bar chart.
  - **Pass 2 Results** — assignments from the second solve pass (if any
    shifts were not filled by a preferred person in pass 1).
- Download results as CSV or JSON.

---

## Sample Datasets

| Dataset | Shifts file | People file | Shifts | People | Notes |
|---|---|---|---|---|---|
| Small | `sample_data/small/shifts.csv` | `sample_data/small/people.csv` | 24 | 8 | 8 days, 3 slots/day |
| Large (6-slot) | `sample_data/shifts_large.csv` | `sample_data/people_large.csv` | 1092 | 200 | 26 weeks, 6 slots/day, includes `points` column |
| Mu2e example | `sample_data/example-mu2e/shifts_mu2e.csv` | `sample_data/example-mu2e/people_mu2e.csv` | 546 | 96 | 26 weeks, day/evening/night |

The large dataset is generated by `generate_sample_data.py` and includes
`institution` assignments cycling through eight institutions (Fermilab,
Argonne, University of Minnesota, University of Michigan, Caltech, MIT,
University of Wisconsin, Boston University).

---

## Project Structure

```
mu2e-shift-scheduler/
├── cli.py                      # Command-line entry point
├── run.py                      # Web server launcher
├── config.yaml                 # Default scheduling constraints and shift-point rules
├── requirements.txt
├── generate_sample_data.py     # Generates the large 6-slot dataset
├── generate_mu2e_data.py       # Generates the Mu2e 3-slot dataset
├── generate_mu2e_blocks.py     # Generates the Mu2e blocks dataset
├── json_to_people_csv.py       # Converts preferences.json → people.csv
├── scheduler/
│   ├── loader.py               # CSV parsing, Shift/Person dataclasses, constraint building
│   ├── solver.py               # PuLP ILP formulation (two-pass)
│   └── exporter.py             # CSV/JSON output, per-person and per-institution stats
├── app/
│   ├── __init__.py             # Flask app factory
│   ├── routes.py               # Scheduler web routes (upload → solve → results → download)
│   ├── preferences.py          # Preference submission blueprint
│   └── templates/
│       ├── base.html
│       ├── welcome.html
│       ├── about.html
│       ├── index.html          # Schedule configuration form
│       ├── results.html        # Assignments table and per-person stats
│       ├── pass2_results.html  # Second-pass assignments
│       ├── institution_stats.html  # Points breakdown by institution
│       └── preferences/
│           ├── index.html      # Preference submission form (list + calendar view)
│           ├── done.html
│           ├── current.html
│           ├── submissions.html
│           └── confirm_overwrite.html
└── sample_data/
    ├── small/
    ├── large/
    └── example-mu2e/
```

---

## License

For internal Mu2e / Fermilab use.
