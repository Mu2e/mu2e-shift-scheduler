# Mu2e Shift Scheduler

## Project Overview

Operations shift scheduler for the Mu2e experiment: collaborators submit
ranked shift preferences through a Flask web app, an ILP optimizer
(PuLP + CBC) assigns people to shifts under per-person point constraints, and
the results are published on a taxonomy-tabbed calendar. Schedules are stored
under global names with classifications ("General Shifts", "Run Coordinators",
"Oncall DAQ Experts") in SQLite, with block CSVs as the interchange format.
Production runs on Fermilab OKD at https://mu2e-shifts.fnal.gov (test instance
https://mu2e-okd-test.fnal.gov).

## Setup and Running

```bash
./bootstrap.sh --admin-password <pass>      # venv + deps + tests + start server
./scripts/start-mu2e-shift-scheduler        # (re)start; --prod for gunicorn
./scripts/stop-mu2e-shift-scheduler         # stop
```

Dev server: http://127.0.0.1:8001, local admin login `mu2e-admin@fnal.gov` /
the seeded password (form shown when `SHOW_ADMIN_LOGIN=1`). Windows
counterparts: `bootstrap.ps1`, `scripts/*.ps1`. Deployment:
`scripts/deploy-okd.sh` (see `man/deploy-okd.1` and `docs/OKD-Deployment.md`).
Configuration precedence: command line > environment > `.env` > `config/config.yaml` > defaults.

## Architecture

- `scheduler/` — pure logic, no Flask: `loader.py` (CSV/YAML parsing,
  `Shift`/`Person`, points defaults), `solver.py` (two-pass ILP,
  `InfeasibleError`), `blocks.py` (multi-day expansion `expand_shift_days`,
  Shift Setup generation `BlockSpec`/`ShiftSpec`/`generate_schedule_rows`,
  block-CSV serialization), `exporter.py` (CSV/JSON export, stats).
- `app/` — Flask blueprints: `routes.py` (calendar, named schedules, solve,
  results, files API), `admin_routes.py` (`/admin`: shift setup, taxonomy,
  settings), `preferences.py` (collects preferences for the admin-selected
  stored schedule — `settings.preferences_schedule_id` — falling back to the
  legacy `PREFERENCES_SHIFTS_CSV` file when unset), `auth_routes.py` + `auth.py` (flask-login,
  Fermilab OIDC, stdlib-sqlite3 users DB, global login requirement),
  `store.py` (ALL app SQLite persistence — schedules, shifts, assignments,
  classifications, settings, contacts; WAL; single-replica constraint),
  `calendar_data.py` (view payloads). Templates in `app/templates/`
  (Jinja2 + Bootstrap 5 CDN, shared macros in `_components.html`), one shared
  vanilla-JS file `app/static/js/mu2e.js`.
- Solver runs synchronously inside `POST /solve`; in-flight results live in
  tempfiles referenced by `session["results_path"]` (per-pod, ephemeral —
  durable persistence is saving to a named schedule or a DATA_DIR JSON).
- Storage: `CSV_DIR` (schedule CSVs, PVC `/app/csv`), `DATA_DIR` (result
  JSONs, `users.sqlite`, `app.sqlite`, PVC `/app/data`).
- Entry points: `run.py` (dev), `wsgi.py` (gunicorn), `cli.py`
  (`mu2e-shift-scheduler solve|serve` console script).

## Configuration Schema (`config/config.yaml`)

```yaml
global:
  target_points_per_person: 2.0   # soft target (deviation penalized)
  min_points_per_person: 1.0      # hard bounds
  max_points_per_person: 4.0
  pass2_min_points_per_person: 0.0  # relaxed bounds for the second pass
  pass2_max_points_per_person: 2.0
shift_points:                     # defaults when shifts CSV has no points col
  default: 1.0
  night: 2.0
  night_start: "20:00"            # night window (wraps midnight)
  night_end: "08:00"
alpha: 1.0                        # preference-vs-fairness tradeoff
overrides: []                     # [{name, min, max, target}] per person
web: {host: "127.0.0.1", port: 8001, debug: false}
```

Instance settings (env or `.env`, see `.env.example`): `CSV_DIR`, `DATA_DIR`,
`AUTH_DB_PATH`, `APP_DB_PATH`, `PREFERENCES_SHIFTS_CSV`, `PREFERENCES_JSON`,
`OIDC_*`, `MU2E_INITIAL_ADMIN_*`, `SHOW_ADMIN_LOGIN`, `SESSION_COOKIE_SECURE`.

## Testing

```bash
venv/bin/python -m pytest tests/     # pytest config in pyproject.toml
```

`tests/conftest.py` builds an isolated app (tmp dirs, seeded admin,
`admin_client`/`user_client` fixtures). Suites: `test_blocks.py`,
`test_generate.py`, `test_store.py`, `test_loader.py`, `test_solver.py`,
`test_routes_{files,calendar,schedules}.py`. Keep every new route/module
covered; page-render smoke tests catch template `url_for` breakage.

## Conventions

- People CSV headers `name`, `institution`, `email`, `phone` are reserved;
  all other columns are ordered preferences.
- Block CSV format: `shift_id, schedule_name, week, block_number, block_name,
  block_type, days, date, date_end, start_time, end_time, shift_type, points`.
- Person string `""`/`UNASSIGNED` renders as "Empty" on calendars.
- All app SQL stays in `app/store.py` (Postgres-compatible `ON CONFLICT`);
  auth SQL stays in `app/auth.py`.
- New scripts get a man page in `man/` (`.1`, groff) and support `--help`.
