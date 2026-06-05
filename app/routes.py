"""
Flask routes for the Mu2e Shift Scheduler web interface.
"""
import csv
import io
import json
import os
import tempfile
import calendar as py_calendar
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from app.auth import admin_required
from scheduler.exporter import as_csv_string, as_json_string, compute_stats, compute_institution_stats
from scheduler.loader import build_constraints, load_config, load_people, load_shifts, validate
from scheduler.solver import InfeasibleError, solve_two_pass as solve

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_session_data(results: list, constraints: dict, config_summary: dict, pass2_results: list = None) -> str:
    """Persist solver results to a temp file; return its path."""
    payload = {
        "results": results,
        "constraints": constraints,
        "config_summary": config_summary,
        "pass2_results": pass2_results or [],
    }
    fd, path = tempfile.mkstemp(suffix=".json", prefix="mu2e_sched_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def _load_session_data():
    """Load persisted solver results from temp file stored in session."""
    payload = _load_session_payload()
    if payload is None:
        return None, None, None, None
    return (
        payload["results"],
        payload["constraints"],
        payload.get("config_summary", {}),
        payload.get("pass2_results", []),
    )


def _load_session_payload() -> dict | None:
    """Load the complete persisted solver payload from the current session."""
    path = session.get("results_path")
    if not path or not Path(path).exists():
        return None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return payload


def _cleanup_old_results() -> None:
    old = session.get("results_path")
    if old and Path(old).exists():
        try:
            os.unlink(old)
        except OSError:
            pass


def _get_int(key: str):
    v = request.form.get(key, "").strip()
    return int(v) if v else None


def _get_float(key: str):
    v = request.form.get(key, "").strip()
    return float(v) if v else None


def _csv_dir() -> Path:
    return Path(current_app.config.get("CSV_DIR", "csv"))


def _data_dir() -> Path:
    return Path(current_app.config.get("DATA_DIR", "data"))


def _result_file_path(filename: str) -> Path:
    safe_name = secure_filename(filename.strip())
    if not safe_name:
        raise ValueError("Enter a file name.")
    if not safe_name.lower().endswith(".json"):
        safe_name = f"{safe_name}.json"
    if safe_name in {"users.sqlite", "preferences.json"}:
        raise ValueError("Choose a different result file name.")
    return _data_dir() / safe_name


def _preferences_shifts_path() -> Path:
    configured = current_app.config.get("PREFERENCES_SHIFTS_CSV")
    if configured:
        return Path(configured)
    return _csv_dir() / "shifts.csv"


def _list_csv_files(csv_dir: Path) -> list[dict]:
    if not csv_dir.exists():
        return []
    files = []
    for path in sorted(csv_dir.glob("*.csv"), key=lambda p: p.name.lower()):
        if path.is_file():
            files.append({
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "is_preferences": path.resolve() == _preferences_shifts_path().resolve(),
            })
    return files


def _load_saved_result_file(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None
    payload.setdefault("constraints", {})
    payload.setdefault("config_summary", {})
    payload.setdefault("pass2_results", [])
    return payload


def _list_result_files() -> list[dict]:
    data_dir = _data_dir()
    if not data_dir.exists():
        return []
    files = []
    for path in sorted(data_dir.glob("*.json"), key=lambda p: p.name.lower()):
        if path.name == "preferences.json" or not path.is_file():
            continue
        payload = _load_saved_result_file(path)
        if payload is None:
            continue
        files.append({
            "name": path.name,
            "size": path.stat().st_size,
            "n_shifts": len(payload.get("results", [])),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return files


def _load_schedule_csv(path: Path) -> list[dict]:
    config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
    load_shifts(str(path), config)

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            shift_id = row.get("shift_id", "").strip()
            if not shift_id:
                continue
            rows.append({
                "shift_id": shift_id,
                "date": row.get("date", "").strip(),
                "date_end": row.get("date_end", "").strip(),
                "start_time": row.get("start_time", "").strip(),
                "end_time": row.get("end_time", "").strip(),
                "points": row.get("points", "").strip() or "",
                "shift_type": row.get("shift_type", "").strip(),
                "block_type": row.get("block_type", "").strip(),
                "person": "",
                "institution": "",
                "is_preferred": True,
            })
    return rows


def _calendar_months(assignments: list[dict]) -> list[dict]:
    by_date: dict[str, list[dict]] = {}
    parsed_dates = []
    for assignment in assignments:
        date_text = str(assignment.get("date", "")).strip()
        try:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        parsed_dates.append(day)
        by_date.setdefault(date_text, []).append(assignment)

    if not parsed_dates:
        return []

    months = []
    year_months = sorted({(day.year, day.month) for day in parsed_dates})
    for year, month in year_months:
        weeks = []
        for week in py_calendar.Calendar(firstweekday=6).monthdatescalendar(year, month):
            days = []
            for day in week:
                date_text = day.isoformat()
                shifts = sorted(
                    by_date.get(date_text, []),
                    key=lambda item: (str(item.get("start_time", "")), str(item.get("shift_id", ""))),
                )
                days.append({
                    "date": day,
                    "date_text": date_text,
                    "in_month": day.month == month,
                    "shifts": shifts,
                })
            weeks.append(days)
        months.append({
            "label": datetime(year, month, 1).strftime("%B %Y"),
            "weeks": weeks,
        })
    return months


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def welcome():
    return render_template("welcome.html")


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/configuration", methods=["GET", "POST"])
@admin_required
def configuration():
    csv_dir = _csv_dir()
    preferences_path = _preferences_shifts_path()

    if request.method == "POST":
        upload = request.files.get("csv_file")
        if not upload or not upload.filename:
            flash("Choose a CSV file to upload.", "danger")
            return redirect(url_for("main.configuration"))

        original_name = secure_filename(upload.filename)
        if not original_name or not original_name.lower().endswith(".csv"):
            flash("Only .csv files can be uploaded.", "danger")
            return redirect(url_for("main.configuration"))

        target_mode = request.form.get("target", "preferences")
        if target_mode == "original":
            target_path = csv_dir / original_name
        else:
            target_path = preferences_path

        target_path.parent.mkdir(parents=True, exist_ok=True)
        upload.save(target_path)
        flash(f"Uploaded {original_name} to {target_path}.", "success")
        return redirect(url_for("main.configuration"))

    return render_template(
        "configuration.html",
        csv_dir=csv_dir,
        preferences_path=preferences_path,
        csv_files=_list_csv_files(csv_dir),
    )


@bp.route("/schedule")
def index():
    # Pre-populate form with current config defaults if available
    config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
    g = config.get("global", {})
    defaults = {
        "target":    g.get("target_points_per_person", g.get("target_shifts_per_person", 3.0)),
        "min":       g.get("min_points_per_person",    g.get("min_shifts_per_person",    1.0)),
        "max":       g.get("max_points_per_person",    g.get("max_shifts_per_person",    5.0)),
        "alpha":     config.get("alpha", 1.0),
        "pass2_min": g.get("pass2_min_points_per_person", g.get("pass2_min_shifts_per_person", 0.0)),
        "pass2_max": g.get("pass2_max_points_per_person", g.get("pass2_max_shifts_per_person", 1000.0)),
    }
    return render_template("index.html", defaults=defaults)


@bp.route("/solve", methods=["POST"])
def run_solve():
    shifts_file = request.files.get("shifts_file")
    people_file = request.files.get("people_file")

    if not shifts_file or not shifts_file.filename:
        flash("A shifts CSV file is required.", "danger")
        return redirect(url_for("main.index"))
    if not people_file or not people_file.filename:
        flash("A people CSV file is required.", "danger")
        return redirect(url_for("main.index"))

    cli_overrides = {
        "target": _get_float("target"),
        "min": _get_float("min"),
        "max": _get_float("max"),
    }
    alpha = _get_float("alpha")
    pass2_min_form = _get_float("pass2_min")
    pass2_max_form = _get_float("pass2_max")

    shifts_path = people_path = None
    try:
        # Write uploads to temp files
        fd, shifts_path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "wb") as f:
            shifts_file.save(f)

        fd, people_path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "wb") as f:
            people_file.save(f)

        # Load config
        config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
        if alpha is not None:
            config["alpha"] = alpha

        # Load and validate data
        shifts = load_shifts(shifts_path, config)
        people = load_people(people_path)
        validate(shifts, people)

        # Build constraints
        constraints = build_constraints(people, config, cli_overrides)

        # Solve
        effective_alpha = alpha if alpha is not None else config.get("alpha", 1.0)
        g = config.get("global", {})
        effective_pass2_min = pass2_min_form if pass2_min_form is not None else float(g.get("pass2_min_points_per_person", g.get("pass2_min_shifts_per_person", 0)))
        effective_pass2_max = pass2_max_form if pass2_max_form is not None else float(g.get("pass2_max_points_per_person", g.get("pass2_max_shifts_per_person", 1000)))
        results, pass2_results = solve(
            shifts, people, constraints,
            alpha=effective_alpha,
            pass2_min=effective_pass2_min,
            pass2_max=effective_pass2_max,
        )

        # Enrich results with institution
        person_inst = {p.name: p.institution for p in people}
        for r in results:
            r["institution"] = person_inst.get(r["person"], "")
        for r in pass2_results:
            r["institution"] = person_inst.get(r["person"], "")

        # Store results
        _cleanup_old_results()
        config_summary = {
            "target":    cli_overrides.get("target") or g.get("target_points_per_person", g.get("target_shifts_per_person", 3.0)),
            "min":       cli_overrides.get("min")    or g.get("min_points_per_person",    g.get("min_shifts_per_person",    1.0)),
            "max":       cli_overrides.get("max")    or g.get("max_points_per_person",    g.get("max_shifts_per_person",    5.0)),
            "alpha":     effective_alpha,
            "pass2_min": effective_pass2_min,
            "pass2_max": effective_pass2_max,
            "n_shifts":  len(shifts),
            "n_people":  len(people),
        }
        session["results_path"] = _save_session_data(results, constraints, config_summary, pass2_results)

    except (ValueError, InfeasibleError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.index"))
    except Exception as exc:
        flash(f"Unexpected error: {exc}", "danger")
        return redirect(url_for("main.index"))
    finally:
        for p in (shifts_path, people_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    return redirect(url_for("main.results"))


@bp.route("/results")
def results():
    data, constraints, config_summary, pass2_results = _load_session_data()
    if data is None:
        flash("No results found. Please run the scheduler first.", "warning")
        return redirect(url_for("main.index"))

    stats = compute_stats(data, constraints)
    return render_template(
        "results.html",
        assignments=data,
        stats=stats,
        config_summary=config_summary,
        has_pass2=bool(pass2_results),
    )


@bp.route("/results/save", methods=["POST"])
def save_results():
    payload = _load_session_payload()
    if payload is None:
        flash("No results to save. Please run the scheduler first.", "warning")
        return redirect(url_for("main.index"))

    filename = request.form.get("filename", "")
    try:
        target_path = _result_file_path(filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.results"))

    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["saved_by"] = getattr(current_user, "email", "")
    payload["saved_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with target_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    flash(f"Saved results to {target_path.name}.", "success")
    return redirect(url_for("main.calendar_view", file=target_path.name))


@bp.route("/results/pass2")
def results_pass2():
    data, constraints, config_summary, pass2_results = _load_session_data()
    if data is None:
        flash("No results found. Please run the scheduler first.", "warning")
        return redirect(url_for("main.index"))
    if not pass2_results:
        flash("All shifts were filled by preferred people — no second pass was needed.", "info")
        return redirect(url_for("main.results"))

    stats = compute_stats(pass2_results, constraints)
    return render_template(
        "pass2_results.html",
        assignments=pass2_results,
        stats=stats,
        config_summary=config_summary,
    )


@bp.route("/calendar")
def calendar_view():
    result_files = _list_result_files()
    schedule_path = _preferences_shifts_path()
    source = request.args.get("source", "results").strip()
    selected_name = request.args.get("file", "").strip()
    if source != "schedule" and not selected_name and result_files:
        selected_name = result_files[0]["name"]

    payload = None
    assignments = []
    months = []
    if source == "schedule":
        selected_name = ""
        if schedule_path.exists():
            try:
                assignments = _load_schedule_csv(schedule_path)
            except ValueError as exc:
                flash(f"Could not load schedule CSV: {exc}", "danger")
            else:
                months = _calendar_months(assignments)
        else:
            flash("No schedule CSV has been uploaded yet.", "warning")
    elif selected_name:
        try:
            selected_path = _result_file_path(selected_name)
        except ValueError as exc:
            flash(str(exc), "danger")
            selected_name = ""
        else:
            payload = _load_saved_result_file(selected_path)
            if payload is None:
                flash(f"Could not load saved results file {selected_name}.", "danger")
                selected_name = ""
            else:
                assignments = payload["results"]
                months = _calendar_months(assignments)

    return render_template(
        "calendar.html",
        result_files=result_files,
        selected_name=selected_name,
        source=source,
        assignments=assignments,
        months=months,
        config_summary=(payload or {}).get("config_summary", {}),
        data_dir=_data_dir(),
        schedule_path=schedule_path,
    )


@bp.route("/calendar/upload", methods=["POST"])
@admin_required
def upload_calendar_schedule():
    upload = request.files.get("schedule_file")
    if not upload or not upload.filename:
        flash("Choose a schedule CSV file to upload.", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))

    original_name = secure_filename(upload.filename)
    if not original_name or not original_name.lower().endswith(".csv"):
        flash("Only .csv schedule files can be uploaded.", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))

    fd, temp_path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "wb") as f:
            upload.save(f)
        _load_schedule_csv(Path(temp_path))

        target_path = _preferences_shifts_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        Path(temp_path).replace(target_path)
    except ValueError as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        flash(f"Invalid schedule CSV: {exc}", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))
    except OSError as exc:
        flash(f"Could not save schedule CSV: {exc}", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))

    flash(f"Updated calendar schedule from {original_name}.", "success")
    return redirect(url_for("main.calendar_view", source="schedule"))


@bp.route("/results/by-institution")
def results_by_institution():
    data, constraints, config_summary, _ = _load_session_data()
    if data is None:
        flash("No results found. Please run the scheduler first.", "warning")
        return redirect(url_for("main.index"))
    inst_stats = compute_institution_stats(data)
    return render_template(
        "institution_stats.html",
        inst_stats=inst_stats,
        config_summary=config_summary,
    )


@bp.route("/download/csv")
def download_csv():
    data, constraints, _, _p2 = _load_session_data()
    if data is None:
        flash("No results to download.", "warning")
        return redirect(url_for("main.index"))
    content = as_csv_string(data).encode("utf-8")
    return send_file(
        io.BytesIO(content),
        mimetype="text/csv",
        as_attachment=True,
        download_name="shift_assignments.csv",
    )


@bp.route("/download/json")
def download_json():
    data, constraints, _, _p2 = _load_session_data()
    if data is None:
        flash("No results to download.", "warning")
        return redirect(url_for("main.index"))
    content = as_json_string(data).encode("utf-8")
    return send_file(
        io.BytesIO(content),
        mimetype="application/json",
        as_attachment=True,
        download_name="shift_assignments.json",
    )
