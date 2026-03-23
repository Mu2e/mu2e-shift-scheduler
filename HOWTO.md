# How to Run the Mu2e Shift Scheduler

## Prerequisites

Python 3.9 or later is required.  Install dependencies once:

```bash
pip install -r requirements.txt
```

---

## 1  Command-Line Interface

### Basic usage

```bash
python3 cli.py solve \
    --shifts  sample_data/shifts_mu2e.csv \
    --people  sample_data/people_mu2e.csv \
    --output  results.csv
```

The solver prints a summary to stdout and writes the assignments to
`results.csv`.

### Save as JSON instead

```bash
python3 cli.py solve \
    --shifts  sample_data/shifts_mu2e.csv \
    --people  sample_data/people_mu2e.csv \
    --output  results.json
```

The output format is inferred from the file extension (`.csv` or `.json`).
You can also force it with `--format csv` or `--format json`.

### Override constraints

These flags override the values in `config.yaml`:

| Flag | Meaning |
|---|---|
| `--target N` | Soft target shifts per person |
| `--min N` | Hard minimum shifts per person |
| `--max N` | Hard maximum shifts per person |
| `--alpha F` | Load-balancing weight (float) |

```bash
python3 cli.py solve \
    --shifts  sample_data/shifts_mu2e.csv \
    --people  sample_data/people_mu2e.csv \
    --output  results.csv \
    --target 6 --min 4 --max 8 --alpha 2.0
```

### Use a different config file

```bash
python3 cli.py solve \
    --config  my_config.yaml \
    --shifts  shifts.csv \
    --people  people.csv \
    --output  results.csv
```

### Full help

```bash
python3 cli.py --help
python3 cli.py solve --help
```

---

## 2  Web Interface

### Start the server

```bash
python3 cli.py serve
```

Then open **http://127.0.0.1:5000/** in your browser.

Or use the convenience launcher:

```bash
python3 run.py
```

### Custom host / port

```bash
python3 cli.py serve --host 0.0.0.0 --port 8080
# or
python3 run.py --host 0.0.0.0 --port 8080
```

### Debug mode (auto-reload on code changes)

```bash
python3 cli.py serve --debug
```

### Using the web interface

1. **Open** http://127.0.0.1:5000/
2. **Upload** your `shifts.csv` and `people.csv` files using the file
   pickers on the Configure page.
3. **Set constraints** — target, minimum, and maximum shifts per person.
   The alpha field controls the fairness/preference tradeoff (expand
   "Advanced" to see it).
4. **Click "Run Scheduler"** — the solver runs and you are redirected to
   the Results page.
5. **Review results** — the assignments table is sortable (click any
   column header) and filterable (type in the search box).  Green rows
   are preferred assignments; uncoloured rows are non-preferred.
6. **Download** — use the "Download CSV" or "Download JSON" buttons to
   save the assignment list.

---

## 3  Configuration File (`config.yaml`)

Edit `config.yaml` to set persistent defaults:

```yaml
global:
  target_shifts_per_person: 6   # soft target
  min_shifts_per_person: 4      # hard lower bound
  max_shifts_per_person: 8      # hard upper bound

alpha: 1.0   # load-balancing penalty weight
             # 0 = ignore target, maximise preferences only
             # 10 = strongly enforce equal shift counts

overrides: []   # per-person exceptions (see below)
```

### Per-person overrides

```yaml
overrides:
  - name: "Alice Smith"
    target: 3
    min: 2
    max: 4
  - name: "Bob Smith"
    max: 2      # Bob can take at most 2 shifts; target/min use global defaults
```

Names must match exactly (case-sensitive) the values in the people CSV.

---

## 4  Input File Format

### shifts.csv

```
shift_id,date,start_time,end_time
shift-W-001,2026-04-06,08:00,16:00
shift-W-002,2026-04-06,16:00,23:59
shift-W-003,2026-04-06,00:00,08:00
```

- `shift_id` must be unique.
- `date` must be ISO 8601 (`YYYY-MM-DD`).
- Times are 24-hour strings (`HH:MM`).

### people.csv

```
name,pref_1,pref_2,pref_3,...
Alice Smith,shift-W-001,shift-W-007,shift-W-013
Bob Smith,shift-E-001,shift-E-004
```

- The first column must be `name`.
- All remaining columns are preferred shift IDs in order of preference
  (most preferred first).
- A person may have any number of preference columns; empty cells are
  ignored.
- Preference shift IDs must exist in the shifts file.

---

## 5  Output Format

### CSV

```
shift_id,date,start_time,end_time,person,is_preferred,pref_rank
shift-W-001,2026-04-06,08:00,16:00,Alice Smith,True,1
shift-W-002,2026-04-06,16:00,23:59,Bob Smith,False,
```

- `is_preferred` — `True` if the assigned person listed this shift as a
  preference, `False` otherwise.
- `pref_rank` — 1-based rank of the preference (1 = most preferred);
  empty when `is_preferred` is `False`.

### JSON

```json
[
  {
    "shift_id": "shift-W-001",
    "date": "2026-04-06",
    "start_time": "08:00",
    "end_time": "16:00",
    "person": "Alice Smith",
    "is_preferred": true,
    "pref_rank": 1
  },
  ...
]
```

---

## 6  Generating Sample Data

```bash
# Original small dataset (24 shifts, 8 people)
# — already included in sample_data/

# Large dataset (1092 shifts, 200 people, 6 slots/day)
python3 generate_sample_data.py

# Mu2e dataset (546 shifts, 96 people, day/evening/night)
python3 generate_mu2e_data.py
```

---

## 7  Troubleshooting

### "Infeasible: total max capacity … is less than the number of shifts"

Increase `max_shifts_per_person` in `config.yaml`, add more people, or
reduce the number of shifts.  The solver prints the exact numbers to help
you diagnose the gap.

### "target_shifts must be between min and max"

Your `target` value falls outside the `[min, max]` range.  Fix the values
in `config.yaml` or via CLI flags so that `min ≤ target ≤ max`.

### The solver takes a very long time

For large problems (200+ people, 1000+ shifts) CBC can take several
minutes.  Options:

- Reduce `alpha` to 0 (disables load-balancing, dramatically simplifies
  the objective).
- Reduce the number of preference columns per person (fewer variables with
  non-zero objective coefficients helps the LP relaxation).
- Use a commercial solver (Gurobi, CPLEX) by changing `PULP_CBC_CMD` in
  `scheduler/solver.py` to the appropriate PuLP solver class.

### Flask "No results found" after solving

Session data is stored in a server-side temp file linked from a browser
cookie.  If you restart the server between solving and viewing results, the
session is lost.  Re-upload your files and solve again.
