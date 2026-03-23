# Mu2e Shift Scheduler

An Integer Linear Programming (ILP)-based shift scheduling tool for the
[Mu2e experiment](https://mu2e.fnal.gov/) at Fermilab.  Given a list of
shifts and a list of people with their preferences, it finds an assignment
that fills every shift with exactly one person while respecting per-person
workload constraints and maximising preference satisfaction.

---

## Features

- **ILP solver** — uses [PuLP](https://coin-or.github.io/pulp/) + the
  bundled CBC solver; no external solver installation required.
- **Preference ranking** — people list shifts in order of preference; the
  most-preferred shift scores highest in the objective function.
- **Configurable constraints** — per-person target, minimum, and maximum
  shift counts set via a YAML config file and/or command-line arguments.
- **Fairness vs. preference tradeoff** — an `alpha` parameter controls how
  heavily the solver penalises deviation from the per-person target count.
- **CLI interface** — `python3 cli.py solve` for scripted/batch use.
- **Web interface** — upload files, run the solver, and download results
  from a browser-based UI.
- **CSV and JSON output** — assignments can be exported in either format.

---

## Input File Formats

### shifts.csv

One row per shift; column order does not matter.

| Column | Description |
|---|---|
| `shift_id` | Unique identifier string, e.g. `shift-W-001` |
| `date` | ISO 8601 date, e.g. `2026-04-06` |
| `start_time` | 24-hour time, e.g. `08:00` |
| `end_time` | 24-hour time, e.g. `16:00` |

```csv
shift_id,date,start_time,end_time
shift-W-001,2026-04-06,08:00,16:00
shift-W-002,2026-04-06,16:00,23:59
shift-W-003,2026-04-06,00:00,08:00
```

### people.csv

One row per person.  The first column is `name`; all subsequent columns are
preferred shift IDs listed in order of preference (most preferred first).
People may list any number of preferences; empty cells are ignored.  Each
person must list at least one preference, though more yields better results.

```csv
name,pref_1,pref_2,pref_3,pref_4
Alice Smith,shift-W-001,shift-W-007,shift-W-013,shift-W-019
Bob Smith,shift-E-001,shift-E-004,shift-E-007
```

---

## Scheduling Model

### Decision variables

`x[i][j]` — binary variable: 1 if person *i* is assigned to shift *j*.

### Objective (maximised)

```
  preference_score - alpha * load_deviation
```

- **preference_score** — sum of rank-weighted preference matches across all
  assignments (first preference scores *N*, second scores *N-1*, etc.).
- **load_deviation** — sum of |actual\_shifts\_i - target\_i| for every
  person, linearised with auxiliary variables.
- **alpha** — scalar controlling the preference/fairness tradeoff (see
  `config.yaml`).

### Hard constraints

1. Each shift is assigned to **exactly one** person.
2. Each person's total shifts is within their `[min, max]` range.

### Soft constraint

Each person's total shifts should be as close as possible to `target`
(penalised in the objective, not enforced as a hard bound).

---

## Configuration (`config.yaml`)

```yaml
global:
  target_shifts_per_person: 6   # soft target
  min_shifts_per_person: 4      # hard lower bound
  max_shifts_per_person: 8      # hard upper bound

alpha: 1.0   # load-balancing weight

overrides:
  - name: "Alice Smith"
    min: 2
    max: 4
    target: 3
```

All global values can also be overridden on the command line (see
`python3 cli.py solve --help`).

---

## Sample Datasets

| Dataset | File pair | Shifts | People | Notes |
|---|---|---|---|---|
| Small (original) | `shifts.csv` / `people.csv` | 24 | 8 | 8 days, 3 shifts/day |
| Large (6-slot) | `shifts_large.csv` / `people_large.csv` | 1092 | 200 | 26 weeks, 6 shifts/day |
| Mu2e (3-slot) | `shifts_mu2e.csv` / `people_mu2e.csv` | 546 | 96 | 26 weeks, day/evening/night |

The Mu2e dataset uses two named shift blocks:

- **`shift-W-NNN`** — weekday shifts (Mon–Thu)
- **`shift-E-NNN`** — weekend shifts (Fri–Sun)

Shift times are the same for both blocks:

| Slot | Start | End |
|---|---|---|
| Day | 08:00 | 16:00 |
| Evening | 16:00 | 23:59 |
| Night | 00:00 | 08:00 |

---

## Project Structure

```
mu2e-shift-scheduler/
├── cli.py                    # Command-line entry point
├── run.py                    # Web server convenience launcher
├── config.yaml               # Default scheduling constraints
├── requirements.txt
├── generate_sample_data.py   # Generates the large 6-slot dataset
├── generate_mu2e_data.py     # Generates the Mu2e 3-slot dataset
├── scheduler/
│   ├── loader.py             # CSV parsing, data classes, constraint building
│   ├── solver.py             # PuLP ILP formulation
│   └── exporter.py           # CSV/JSON output and statistics
├── app/
│   ├── __init__.py           # Flask app factory
│   ├── routes.py             # Upload → solve → results → download
│   └── templates/
│       ├── base.html
│       ├── index.html        # File upload and configuration form
│       └── results.html      # Assignments table and per-person stats
└── sample_data/
    ├── shifts.csv / people.csv
    ├── shifts_large.csv / people_large.csv
    └── shifts_mu2e.csv / people_mu2e.csv
```

---

## License

For internal Mu2e / Fermilab use.
