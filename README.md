# Mu2e Shift Scheduler

This is a simple tool for scheduling shifts on the Mu2e experiment.  I based it
off of our experiences on NOvA with doing similar types of shift scheduling and
in particular trying to handle scheduling regular shifts and expert on-call shifts.

The tool is broken down into a couple parts.

1. A Calendar (it shows who is on shift and who will be on shift)
2. A tool for "defining" what a shift is (i.e. time range, date range etc...)
3. A tool for letting people enter their preference for when they want to be on shift
4. And optimizer which take the schedule and people's preferences and compute a solution

This is NOT the Gary Feldman scheduler!!!!  (Gary is great but OMG I don't want to
deal with how he Monte Carlo's things). Instead it's a simple multi-pass linear optimization
problem.  Does it work?  Yeah....Mostly....in fact if the pool of people and the shifts they
select are reasonable, it does a great job.  If people don't select enough shifts so that 
there are holes in the calendar's coverage...well it doesn't do as well (but it does find
a solution!)

So more formally what you are looking at is:

A shift scheduling tool for the Mu2e experiment at Fermilab. It takes a set of
defined shifts (arbitrary time blocks) and a list of people with ranked
preferences, then finds an optimal assignment using integer linear programming.
Constraints such as per-person shift-point targets, minimums, maximums, and a
fairness/preference tradeoff weight are all configurable (and yes it does have weighting
and preferences)

The tool includes a web interface for participants to submit preferences, and
for coordinators to run and review the schedule.  All data is stored in
human-readable CSV and JSON formats.  Which can be saved/exported, and there
are commandline tools for doing certain things (like a crazy large solve).

The whole thing is deploy-able via a container, and we are currently running
a production version on the FNAL OKD cluster (talk to Andrew for details)

## Getting Started

Fresh checkout — one command sets up the venv, installs dependencies, runs the
test suite, and starts the development server on http://127.0.0.1:8001:

```
./bootstrap.sh --admin-password <password-for-local-admin>
```

Day-to-day the server is managed with the start/stop scripts (safe to re-run;
the start script updates dependencies and restarts a running server):

```
./scripts/start-mu2e-shift-scheduler          # dev server on 127.0.0.1:8001
./scripts/start-mu2e-shift-scheduler --prod   # gunicorn on 0.0.0.0
./scripts/stop-mu2e-shift-scheduler           # graceful stop
```

Log in with the local admin form (`mu2e-admin@fnal.gov` / your password);
in development `SHOW_ADMIN_LOGIN=1` is the default. Local settings live in
`.env` (created from `.env.example` by bootstrap); precedence is
command line > environment > `.env` > `config/config.yaml` > defaults.
Windows users: `bootstrap.ps1` and `scripts\*.ps1` provide the same flow.

The package is pip-installable (`pyproject.toml`); the venv gets a
`mu2e-shift-scheduler` console command with `solve` (batch scheduling) and
`serve` subcommands. Man pages for every script are in `man/` — e.g.
`man ./man/start-mu2e-shift-scheduler.1`. There are also generator tools under
`scripts/` for producing shift CSVs for different time windows; see their
`--help` and the samples in `sample_data/`.

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
- **Per-person overrides** — individual constraints can be set in `config/config.yaml`
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
- **Named schedules** — schedules are stored under a global name
  (e.g. "Fall 2026") with a classification from an admin-managed taxonomy
  ("General Shifts", "Run Coordinators", "Oncall DAQ Experts", ...);
  re-submitting an existing name overwrites it after confirmation.
- **Calendar views** — the default post-login page: one tab per
  classification, month/week/today views with month and year navigation,
  an admin-selected default calendar, multi-day shift blocks expanded onto
  every covered day ("Day: Bob" on each day), "Empty" for unassigned
  shifts, and assigned names linking to contact info (mailto/phone).
- **Shift Setup page** — admins construct a schedule in the browser:
  date range, shifts per day (name, time range, float weight), shift
  length in days, repetition rate (day/week/2-week/month), and weekday
  checkboxes; "Generate schedule" stores it and writes a matching CSV.
- **Server file storage** — every browse button accepts a local file or a
  file from container storage; upload, download, and export endpoints
  cover both the CSV and data directories.
- **Token API** — `GET /api/v1/on-duty` reports who is on duty at a given
  instant (with contact details), plus `/classifications` and `/schedules`.
  Bearer tokens are minted and revoked on Admin > API Tokens, so consumers
  such as the Mu2e Bulletin Board need no SSO session. See
  [docs/API.md](docs/API.md) and `scripts/shifts_api_probe.py`.

---

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

The suite covers the block/date logic (`scheduler/blocks.py`), the SQLite
store (`app/store.py`), loader/solver baselines, and the web routes
(calendar views, named schedules, shift setup, file storage).

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
| `points` | no | Decimal point value for this shift. If omitted, defaults to `2.0` for night shifts and `1.0` otherwise (thresholds configurable in `config/config.yaml`). |

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

## Configuration (`config/config.yaml`)

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

### Shift self-signup (`/signup` and the calendar menu)

Any logged-in shifter can sign up for an **open** shift, or for every shift
of a Shift Setup block at once, and remove themselves from shifts they
signed up for:

- **Calendar:** right-click a shift for *Sign me up for this shift*, *Sign me
  up for the whole block (N shifts)* or *Remove me from this shift*.
  Administrators see these items above their Assign / Remind / Clear items.
- **Sign-Up page (`/signup`):** pick a schedule; open shifts are grouped by
  block with per-shift and whole-block buttons, and *My upcoming shifts*
  lists the user's shifts with *Remove me*.

The person written to the schedule is the account's display name (SSO name,
or the name chosen on My Profile). Institution, email and phone come from the
profile, with blanks filled from the Mu2e Collaboration directory (matched by
email, then FNAL username). The directory name also counts as "you" when
deciding which existing assignments belong to the user.

Guards (all enforced server-side, inside one `BEGIN IMMEDIATE` transaction):
the shift must be empty (`""`/`UNASSIGNED`); it must not have started or be
inside `signup_cutoff_hours`; it must not overlap a shift the user already
holds in that schedule; a block claim is all or nothing. Releases are limited
to the user's own self-signups unless `allow_release_assigned` is set, and
are refused inside `release_cutoff_hours`. Every claim and release is written
to `signup_log` (shown to admins on the Sign-Up page), and
`assignments.source` records `solver`, `admin` or `signup` for each row.
Admins can close an individual schedule to self-signup on the Schedules page.

```yaml
signup:                          # config/config.yaml
  enabled: true                  # MU2E_SIGNUP_ENABLED / serve --[no-]signup
  allow_block_signup: true       # MU2E_SIGNUP_ALLOW_BLOCK
  allow_release_assigned: false  # MU2E_SIGNUP_ALLOW_RELEASE_ASSIGNED
  signup_cutoff_hours: 0         # MU2E_SIGNUP_CUTOFF_HOURS
  release_cutoff_hours: 24       # MU2E_SIGNUP_RELEASE_CUTOFF_HOURS
  require_collab_member: false   # MU2E_SIGNUP_REQUIRE_COLLAB_MEMBER
```

```bash
venv/bin/mu2e-shift-scheduler serve --port 8001 --signup-release-cutoff-hours 48
```

### Collaboration directory (`/admin/collaboration`)

The member directory is synced read-only from the Mu2e Collaboration database
(https://mu2e-collaboration.fnal.gov, REST API v1) into `app.sqlite`
(`collab_members`). Active collaboration members appear in shift-assignment
name suggestions, and their email, phone and institution fill blank fields in
calendar contact popovers, reminders and the user's profile page. Precedence:
the user's own profile > existing contacts > the collaboration directory.
People are matched by email, then FNAL username, then first+last name
(directory names are `Last, First Middle`; ambiguous names are not matched).

Configure a token (never commit it):

```bash
# .env (loaded by scripts/start-mu2e-shift-scheduler)
MU2E_COLLAB_TOKEN_FILE=mu2e-collaboration-api-access-token
```

The app then syncs every `MU2E_COLLAB_SYNC_HOURS` (default 6; a SQLite lease
lets only one gunicorn worker run it). Run or inspect a sync by hand:

```bash
venv/bin/mu2e-shift-scheduler collab-sync --token-file mu2e-collaboration-api-access-token --full
venv/bin/mu2e-shift-scheduler collab-sync --status
```

On OKD, set `collaboration.token` in the private `my-values.yaml` (rendered
into the release Secret as `MU2E_COLLAB_TOKEN`). Use a **read-only** token
owned by a collaboration admin; the admin owner is what allows phone lookups.

---

## Sample Datasets

| Dataset | Shifts file | People file | Shifts | People | Notes |
|---|---|---|---|---|---|
| Small | `sample_data/small/shifts.csv` | `sample_data/small/people.csv` | 24 | 8 | 8 days, 3 slots/day |
| Large (6-slot) | `sample_data/shifts_large.csv` | `sample_data/people_large.csv` | 1092 | 200 | 26 weeks, 6 slots/day, includes `points` column |
| Mu2e example | `sample_data/example-mu2e/shifts_mu2e.csv` | `sample_data/example-mu2e/people_mu2e.csv` | 546 | 96 | 26 weeks, day/evening/night |

The large dataset is generated by `scripts/generate_sample_data.py` and includes
`institution` assignments cycling through eight institutions (Fermilab,
Argonne, University of Minnesota, University of Michigan, Caltech, MIT,
University of Wisconsin, Boston University).

To create a custom block schedule interactively, run:

```bash
python3 scripts/create_shift_blocks.py
```

The script prompts for the schedule name, date range, weekly block layout,
days in each block, and daily shift times, then writes a scheduler-compatible
CSV under `sample_data/` by default.

---

## Project Structure

```
mu2e-shift-scheduler/
├── cli.py                      # Command-line entry point
├── run.py                      # Web server launcher
├── config/
│   ├── config.yaml             # Default scheduling constraints and shift-point rules
│   ├── profiles.yaml           # Preference profiles for large sample generation
│   └── profiles_blocks.yaml    # Preference profiles for block sample generation
├── requirements.txt
├── json_to_people_csv.py       # Converts preferences.json → people.csv
├── scripts/
│   ├── create_shift_blocks.py  # Interactively creates custom shift-block CSVs
│   ├── generate_sample_data.py # Generates the large 6-slot dataset
│   ├── generate_mu2e_data.py   # Generates the Mu2e 3-slot dataset
│   └── generate_mu2e_blocks.py # Generates the Mu2e blocks dataset
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
