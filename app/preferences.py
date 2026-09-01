"""
Flask blueprint for collecting shift preferences from experiment participants.

Configuration (set on the Flask app):
    PREFERENCES_SHIFTS_CSV   Path to the shifts CSV file (required).
    PREFERENCES_JSON         Path to the output JSON file (default: preferences.json).

When an admin has selected a preference-collection schedule, every submission
is tagged with the schedule name and the current per-person rankings are also
written server-side as a solver-ready people CSV named
``<schedule-slug>-prefs.csv`` in CSV_DIR.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user

from app import store
from scheduler.loader import load_config, load_shifts

bp = Blueprint("preferences", __name__, url_prefix="/preferences")


def _shifts_csv_path() -> Path:
    p = current_app.config.get("PREFERENCES_SHIFTS_CSV")
    if not p:
        raise RuntimeError("PREFERENCES_SHIFTS_CSV is not configured.")
    return Path(p)


def _load_preference_shifts() -> tuple[list, dict | None]:
    """Shifts open for preference collection.

    Uses the schedule the admin selected on the Configuration page; when none
    is set (or it was deleted), falls back to the legacy PREFERENCES_SHIFTS_CSV
    file. Returns (shifts, schedule) where schedule is the stored-schedule dict
    or None for the legacy CSV path.
    """
    schedule_id = store.get_preferences_schedule_id()
    if schedule_id:
        schedule = store.get_schedule(schedule_id)
        if schedule:
            config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
            return store.schedule_to_shift_objects(schedule_id, config), schedule
    return load_shifts(str(_shifts_csv_path())), None


def _json_path() -> Path:
    p = current_app.config.get("PREFERENCES_JSON", "preferences.json")
    return Path(p)


def _load_submissions() -> list:
    path = _json_path()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_existing_index(submissions: list, name: str) -> int:
    """Return the index of the most recent entry for this name, or -1 if not found."""
    key = name.strip().lower()
    for i in range(len(submissions) - 1, -1, -1):
        if submissions[i]["name"].strip().lower() == key:
            return i
    return -1


def _build_entry(name: str, preferences: list[str], schedule_name: str = "") -> dict:
    return {
        "name": name.strip(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "preferences": preferences,
        "schedule": schedule_name.strip(),
    }


def _write_submissions(submissions: list) -> None:
    path = _json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(submissions, f, indent=2)


def _save_submission(name: str, preferences: list[str], schedule_name: str = "") -> None:
    submissions = _load_submissions()
    submissions.append(_build_entry(name, preferences, schedule_name))
    _write_submissions(submissions)


def _overwrite_submission(name: str, preferences: list[str], schedule_name: str = "") -> None:
    """Replace the existing entry for *name* in-place; append if not found."""
    submissions = _load_submissions()
    idx = _find_existing_index(submissions, name)
    entry = _build_entry(name, preferences, schedule_name)
    if idx >= 0:
        submissions[idx] = entry
    else:
        submissions.append(entry)
    _write_submissions(submissions)


def _current_preferences_schedule() -> dict | None:
    schedule_id = store.get_preferences_schedule_id()
    if schedule_id:
        return store.get_schedule(schedule_id)
    return None


def _prefs_csv_path(schedule: dict) -> Path:
    csv_dir = Path(current_app.config.get("CSV_DIR", "csv"))
    return csv_dir / f"{schedule['slug']}-prefs.csv"


def _write_schedule_prefs_csv(schedule: dict) -> Path:
    """Regenerate the solver-ready people CSV for one schedule's submissions.

    Includes the most recent submission per person among entries tagged with
    the schedule's name. Format matches json_to_people_csv.py:
    name, pref_1..pref_N (rows padded so the loader never sees missing cells).
    """
    key = schedule["name"].strip().lower()
    latest: dict[str, dict] = {}
    for sub in _load_submissions():
        if str(sub.get("schedule", "")).strip().lower() != key:
            continue
        person = sub["name"].strip().lower()
        if person not in latest or sub["submitted_at"] > latest[person]["submitted_at"]:
            latest[person] = sub
    subs = sorted(latest.values(), key=lambda s: s["name"].strip().lower())

    max_prefs = max((len(s["preferences"]) for s in subs), default=0)
    header = ["name"] + [f"pref_{i + 1}" for i in range(max_prefs)]
    path = _prefs_csv_path(schedule)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for sub in subs:
            prefs = list(sub["preferences"])
            writer.writerow([sub["name"].strip()] + prefs + [""] * (max_prefs - len(prefs)))
    return path


def _record_submission(name: str, preferences: list[str], overwrite: bool) -> None:
    """Persist one submission to the JSON log and, when collecting for a named
    schedule, refresh that schedule's server-side <slug>-prefs.csv file."""
    schedule = _current_preferences_schedule()
    schedule_name = schedule["name"] if schedule else ""
    if overwrite:
        _overwrite_submission(name, preferences, schedule_name)
    else:
        _save_submission(name, preferences, schedule_name)
    if schedule:
        _write_schedule_prefs_csv(schedule)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    preferences_schedule = None
    try:
        shifts, preferences_schedule = _load_preference_shifts()
    except Exception as exc:
        flash(f"Could not load shifts: {exc}", "danger")
        shifts = []
    default_name = ""
    if current_user.is_authenticated:
        default_name = getattr(current_user, "name", "") or getattr(current_user, "email", "")
    return render_template(
        "preferences/index.html",
        shifts=shifts,
        default_name=default_name,
        preferences_schedule=preferences_schedule,
    )


@bp.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Please enter your name.", "danger")
        return redirect(url_for("preferences.index"))

    preferences = request.form.getlist("pref[]")
    preferences = [p for p in preferences if p.strip()]

    if not preferences:
        flash("Please add at least one preferred shift.", "danger")
        return redirect(url_for("preferences.index"))

    try:
        submissions = _load_submissions()
        idx = _find_existing_index(submissions, name)
        if idx >= 0:
            # Duplicate — stash pending data and ask for confirmation
            session["pending_preference"] = {"name": name, "preferences": preferences}
            existing = submissions[idx]
            return render_template(
                "preferences/confirm_overwrite.html",
                name=name,
                existing=existing,
                new_preferences=preferences,
            )

        _record_submission(name, preferences, overwrite=False)
    except Exception as exc:
        flash(f"Error saving preferences: {exc}", "danger")
        return redirect(url_for("preferences.index"))

    return redirect(url_for("preferences.done", name=name))


@bp.route("/overwrite", methods=["POST"])
def overwrite():
    pending = session.pop("pending_preference", None)
    if not pending:
        flash("No pending submission found. Please try again.", "warning")
        return redirect(url_for("preferences.index"))

    try:
        _record_submission(pending["name"], pending["preferences"], overwrite=True)
    except Exception as exc:
        flash(f"Error saving preferences: {exc}", "danger")
        return redirect(url_for("preferences.index"))

    return redirect(url_for("preferences.done", name=pending["name"]))


@bp.route("/done")
def done():
    name = request.args.get("name", "")
    return render_template("preferences/done.html", name=name)


@bp.route("/current")
def current():
    try:
        all_submissions = _load_submissions()
    except Exception as exc:
        flash(f"Could not load submissions: {exc}", "danger")
        all_submissions = []

    # Deduplicate: one entry per person, keeping the most recent
    seen = {}
    for sub in all_submissions:
        key = sub["name"].strip().lower()
        if key not in seen or sub["submitted_at"] > seen[key]["submitted_at"]:
            seen[key] = sub
    current_submissions = list(seen.values())
    current_submissions.sort(key=lambda s: s["name"].strip().lower())

    try:
        shifts, _schedule = _load_preference_shifts()
        shift_map = {s.shift_id: s for s in shifts}
    except Exception:
        shift_map = {}

    schedule = _current_preferences_schedule()
    return render_template(
        "preferences/current.html",
        submissions=current_submissions,
        shift_map=shift_map,
        json_path=str(_json_path().resolve()),
        prefs_csv_name=_prefs_csv_path(schedule).name if schedule else "",
    )


@bp.route("/submissions")
def submissions():
    try:
        all_submissions = _load_submissions()
    except Exception as exc:
        flash(f"Could not load submissions: {exc}", "danger")
        all_submissions = []

    # Count unique names (use the most recent submission per person)
    try:
        shifts, _schedule = _load_preference_shifts()
        shift_map = {s.shift_id: s for s in shifts}
    except Exception:
        shift_map = {}

    schedule = _current_preferences_schedule()
    return render_template(
        "preferences/submissions.html",
        submissions=all_submissions,
        shift_map=shift_map,
        json_path=str(_json_path().resolve()),
        prefs_csv_name=_prefs_csv_path(schedule).name if schedule else "",
    )
