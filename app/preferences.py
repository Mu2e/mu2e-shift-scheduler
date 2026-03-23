"""
Flask blueprint for collecting shift preferences from experiment participants.

Configuration (set on the Flask app):
    PREFERENCES_SHIFTS_CSV   Path to the shifts CSV file (required).
    PREFERENCES_JSON         Path to the output JSON file (default: preferences.json).
"""
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

from scheduler.loader import load_shifts

bp = Blueprint("preferences", __name__, url_prefix="/preferences")


def _shifts_csv_path() -> Path:
    p = current_app.config.get("PREFERENCES_SHIFTS_CSV")
    if not p:
        raise RuntimeError("PREFERENCES_SHIFTS_CSV is not configured.")
    return Path(p)


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


def _build_entry(name: str, preferences: list[str]) -> dict:
    return {
        "name": name.strip(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "preferences": preferences,
    }


def _write_submissions(submissions: list) -> None:
    path = _json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(submissions, f, indent=2)


def _save_submission(name: str, preferences: list[str]) -> None:
    submissions = _load_submissions()
    submissions.append(_build_entry(name, preferences))
    _write_submissions(submissions)


def _overwrite_submission(name: str, preferences: list[str]) -> None:
    """Replace the existing entry for *name* in-place; append if not found."""
    submissions = _load_submissions()
    idx = _find_existing_index(submissions, name)
    entry = _build_entry(name, preferences)
    if idx >= 0:
        submissions[idx] = entry
    else:
        submissions.append(entry)
    _write_submissions(submissions)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    try:
        shifts = load_shifts(str(_shifts_csv_path()))
    except Exception as exc:
        flash(f"Could not load shifts: {exc}", "danger")
        shifts = []
    return render_template("preferences/index.html", shifts=shifts)


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

        _save_submission(name, preferences)
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
        _overwrite_submission(pending["name"], pending["preferences"])
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
        shifts = load_shifts(str(_shifts_csv_path()))
        shift_map = {s.shift_id: s for s in shifts}
    except Exception:
        shift_map = {}

    return render_template(
        "preferences/current.html",
        submissions=current_submissions,
        shift_map=shift_map,
        json_path=str(_json_path().resolve()),
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
        shifts = load_shifts(str(_shifts_csv_path()))
        shift_map = {s.shift_id: s for s in shifts}
    except Exception:
        shift_map = {}

    return render_template(
        "preferences/submissions.html",
        submissions=all_submissions,
        shift_map=shift_map,
        json_path=str(_json_path().resolve()),
    )
