"""
Flask routes for the Mu2e Shift Scheduler web interface.
"""
import csv
import io
import json
import os
import shutil
import tempfile
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

from app import store
from app.auth import admin_required
from scheduler.blocks import rows_to_block_csv_string
from scheduler.exporter import as_csv_string, as_json_string, compute_stats, compute_institution_stats
from scheduler.loader import build_constraints, load_config, load_people, load_shifts, validate
from scheduler.solver import InfeasibleError, solve_two_pass as solve

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_session_data(results: list, constraints: dict, config_summary: dict,
                       pass2_results: list = None, schedule_id: int = None) -> str:
    """Persist solver results to a temp file; return its path."""
    payload = {
        "results": results,
        "constraints": constraints,
        "config_summary": config_summary,
        "pass2_results": pass2_results or [],
        "schedule_id": schedule_id,
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


def _schedule_file_path(filename: str) -> Path:
    safe_name = secure_filename(filename.strip())
    if not safe_name:
        raise ValueError("Choose a schedule file.")
    if not safe_name.lower().endswith(".csv"):
        raise ValueError("Schedule files must be .csv files.")
    return _csv_dir() / safe_name


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


def _parse_anchor_date(raw: str):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _default_anchor(entries: dict):
    """Anchor for views without an explicit date: today, unless the loaded
    schedule has no entries this month — then the nearest month that does."""
    from datetime import date as date_cls

    today = date_cls.today()
    if not entries:
        return today
    entry_dates = sorted(
        d for d in (_parse_anchor_date(text) for text in entries) if d is not None
    )
    if not entry_dates:
        return today
    if any(d.year == today.year and d.month == today.month for d in entry_dates):
        return today
    upcoming = [d for d in entry_dates if d >= today]
    return upcoming[0] if upcoming else entry_dates[-1]


# ---------------------------------------------------------------------------
# Named schedules
# ---------------------------------------------------------------------------

def _clear_staged_schedule() -> None:
    staged = session.pop("schedules_staged", None)
    if staged:
        try:
            os.unlink(staged)
        except OSError:
            pass


def _read_block_csv_rows(path: Path) -> list[dict]:
    """Read a schedule CSV (simple or block format) as raw dict rows after
    validating it through the loader."""
    config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
    load_shifts(str(path), config)
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if (row.get("shift_id") or "").strip()]


@bp.route("/schedules")
def schedules_page():
    schedules = store.list_schedules()
    referenced = {s["csv_filename"] for s in schedules if s["csv_filename"]}
    unimported = [
        f for f in _list_csv_files(_csv_dir())
        if f["name"] not in referenced and not f["is_preferences"]
    ]
    return render_template(
        "schedules.html",
        nav_active="schedules",
        schedules=schedules,
        classifications=store.list_classifications(),
        unimported=unimported,
        default_schedule_id=store.get_default_schedule_id(),
    )


@bp.route("/schedules/save", methods=["POST"])
@admin_required
def schedules_save():
    name = request.form.get("name", "").strip()
    classification_slug = request.form.get("classification", "").strip()
    server_file = request.form.get("server_file", "").strip()
    upload = request.files.get("csv_file")

    classification_id = None
    if classification_slug:
        classification = store.get_classification_by_slug(classification_slug)
        if classification is None:
            flash("Choose a valid classification.", "danger")
            return redirect(url_for("main.schedules_page"))
        classification_id = classification["id"]

    temp_path = None
    try:
        if upload and upload.filename:
            fd, temp_path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "wb") as f:
                upload.save(f)
            rows = _read_block_csv_rows(Path(temp_path))
            source = "upload"
            source_file = Path(temp_path)
        elif request.form.get("from_staged") == "1":
            staged = session.get("schedules_staged", "")
            if not staged or not Path(staged).exists():
                raise ValueError("The uploaded file is no longer available; upload it again.")
            rows = _read_block_csv_rows(Path(staged))
            source = "upload"
            source_file = Path(staged)
        elif server_file:
            path = _schedule_file_path(server_file)
            if not path.exists():
                raise ValueError(f"Server file {server_file} was not found.")
            rows = _read_block_csv_rows(path)
            source = "server_csv"
            source_file = path
        elif request.form.get("from_preview") == "1":
            staged = session.get("calendar_preview")
            if not staged or not Path(staged.get("path", "")).exists():
                raise ValueError("The previewed file is no longer available.")
            with open(staged["path"], encoding="utf-8") as f:
                rows = json.load(f).get("rows", [])
            source = "upload"
            source_file = None
        else:
            raise ValueError("Choose a CSV file (upload or server storage).")

        if not name:
            raise ValueError("Enter a schedule name.")

        existing = store.get_schedule_by_name(name)
        if existing and request.form.get("overwrite") != "1":
            form_fields = {
                "name": [name],
                "classification": [classification_slug],
                "server_file": [server_file],
                "from_preview": [request.form.get("from_preview", "")],
            }
            if temp_path:
                # Stage the upload so the confirm re-post can find it.
                _clear_staged_schedule()
                fd, staged_path = tempfile.mkstemp(suffix=".csv", prefix="mu2e_staged_")
                os.close(fd)
                shutil.copyfile(temp_path, staged_path)
                session["schedules_staged"] = staged_path
                form_fields["from_staged"] = ["1"]
            elif request.form.get("from_staged") == "1":
                form_fields["from_staged"] = ["1"]
            return render_template(
                "schedule_confirm_overwrite.html",
                nav_active="schedules",
                existing=existing,
                action=url_for("main.schedules_save"),
                form_fields=form_fields,
            )

        # Persist a backing CSV so file-based flows keep working.
        backing_name = f"{store.slugify(name)}.csv"
        backing_path = _csv_dir() / backing_name
        schedule_id, dropped = store.upsert_schedule(
            name,
            classification_id,
            rows,
            source=source,
            created_by=getattr(current_user, "email", ""),
            csv_filename=backing_name,
        )
        backing_path.parent.mkdir(parents=True, exist_ok=True)
        if source_file is not None and source_file.resolve() != backing_path.resolve():
            shutil.copyfile(source_file, backing_path)
        elif source_file is None:
            backing_path.write_text(rows_to_block_csv_string(rows), encoding="utf-8")
        if request.form.get("from_staged") == "1":
            _clear_staged_schedule()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.schedules_page"))
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    message = f"Saved schedule '{name}'."
    if dropped:
        message += f" Dropped {dropped} saved assignment(s) for removed shifts."
    flash(message, "success")
    return redirect(url_for("main.calendar_view", schedule=name))


@bp.route("/schedules/<int:schedule_id>/delete", methods=["POST"])
@admin_required
def schedules_delete(schedule_id: int):
    schedule = store.get_schedule(schedule_id)
    if schedule is None:
        flash("Schedule not found.", "danger")
    else:
        store.delete_schedule(schedule_id)
        if store.get_default_schedule_id() == schedule_id:
            store.set_default_schedule_id(None)
        flash(f"Deleted schedule '{schedule['name']}'. Its backing CSV file was kept.", "success")
    return redirect(url_for("main.schedules_page"))


@bp.route("/schedules/<int:schedule_id>/export.csv")
def schedules_export_csv(schedule_id: int):
    schedule = store.get_schedule(schedule_id)
    if schedule is None:
        flash("Schedule not found.", "danger")
        return redirect(url_for("main.schedules_page"))
    rows = []
    for row in store.get_schedule_shifts(schedule_id):
        row = dict(row)
        row["schedule_name"] = schedule["name"]
        row["shift_type"] = row.get("shift_name", "")
        row["points"] = "" if row.get("points") is None else row["points"]
        rows.append(row)
    content = rows_to_block_csv_string(rows).encode("utf-8")
    return send_file(
        io.BytesIO(content),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{schedule['slug']}.csv",
    )


@bp.route("/schedules/<int:schedule_id>/assignments/export.<fmt>")
def schedules_export_assignments(schedule_id: int, fmt: str):
    schedule = store.get_schedule(schedule_id)
    if schedule is None or fmt not in {"csv", "json"}:
        flash("Schedule not found.", "danger")
        return redirect(url_for("main.schedules_page"))
    assignments = store.get_assignments(schedule_id)
    merged = []
    for shift in store.get_schedule_shifts(schedule_id):
        assignment = assignments.get(shift["shift_id"], {})
        merged.append({
            "shift_id": shift["shift_id"],
            "date": shift["date"],
            "start_time": shift["start_time"],
            "end_time": shift["end_time"],
            "points": shift["points"] if shift["points"] is not None else "",
            "person": assignment.get("person", ""),
            "institution": assignment.get("institution", ""),
            "is_preferred": assignment.get("is_preferred", ""),
            "pref_rank": assignment.get("pref_rank", ""),
        })
    if fmt == "csv":
        content = as_csv_string(merged).encode("utf-8")
        mimetype = "text/csv"
    else:
        content = as_json_string(merged).encode("utf-8")
        mimetype = "application/json"
    return send_file(
        io.BytesIO(content),
        mimetype=mimetype,
        as_attachment=True,
        download_name=f"{schedule['slug']}-assignments.{fmt}",
    )


# ---------------------------------------------------------------------------
# Container file storage (browse / download / upload)
# ---------------------------------------------------------------------------

# Filenames in the storage dirs that must never be served or overwritten.
_PROTECTED_FILES = {"users.sqlite", "app.sqlite", "preferences.json"}


def _storage_dir(kind: str) -> tuple[Path, set]:
    """Map a storage-dir keyword to (directory, allowed extensions)."""
    if kind == "csv":
        return _csv_dir(), {".csv"}
    if kind == "data":
        return _data_dir(), {".json"}
    raise ValueError("Unknown storage directory.")


def _storage_file_path(kind: str, name: str) -> Path:
    directory, extensions = _storage_dir(kind)
    safe_name = secure_filename((name or "").strip())
    if not safe_name:
        raise ValueError("Choose a file.")
    if Path(safe_name).suffix.lower() not in extensions:
        raise ValueError(f"Only {', '.join(sorted(extensions))} files are allowed here.")
    if safe_name in _PROTECTED_FILES or safe_name.endswith(("-wal", "-shm")):
        raise ValueError("That file is not accessible.")
    path = (directory / safe_name).resolve()
    if path.parent != directory.resolve():
        raise ValueError("Invalid file name.")
    return path


def _list_storage_files(kind: str) -> list[dict]:
    directory, extensions = _storage_dir(kind)
    if not directory.exists():
        return []
    files = []
    for ext in sorted(extensions):
        for path in directory.glob(f"*{ext}"):
            if not path.is_file() or path.name in _PROTECTED_FILES:
                continue
            stat = path.stat()
            files.append({
                "name": path.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    files.sort(key=lambda f: f["name"].lower())
    return files


@bp.route("/api/files")
def api_files():
    kind = request.args.get("dir", "csv").strip()
    try:
        files = _list_storage_files(kind)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return {"dir": kind, "files": files}


@bp.route("/files/download")
def download_stored_file():
    kind = request.args.get("dir", "csv").strip()
    name = request.args.get("name", "")
    try:
        path = _storage_file_path(kind, name)
        if not path.exists():
            raise ValueError("File not found.")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(request.referrer or url_for("main.calendar_view"))
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.route("/files/upload", methods=["POST"])
@admin_required
def upload_stored_file():
    kind = request.form.get("dir", "csv").strip()
    upload = request.files.get("file")
    redirect_target = request.form.get("next") or url_for("main.configuration")
    if not upload or not upload.filename:
        flash("Choose a file to upload.", "danger")
        return redirect(redirect_target)

    try:
        target_path = _storage_file_path(kind, upload.filename)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(redirect_target)

    fd, temp_path = tempfile.mkstemp(suffix=target_path.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            upload.save(f)
        # Validate content before it lands in the storage dir.
        if kind == "csv":
            _load_schedule_csv(Path(temp_path))
        else:
            if _load_saved_result_file(Path(temp_path)) is None:
                raise ValueError("Not a valid saved-results JSON file.")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temp_path, target_path)
    except ValueError as exc:
        flash(f"Invalid file: {exc}", "danger")
        return redirect(redirect_target)
    except OSError as exc:
        flash(f"Could not save file: {exc}", "danger")
        return redirect(redirect_target)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    flash(f"Uploaded {target_path.name}.", "success")
    return redirect(redirect_target)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def welcome():
    if current_user.is_authenticated:
        return redirect(url_for("main.calendar_view"))
    return redirect(url_for("auth.login"))


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
        nav_active="admin",
        csv_dir=csv_dir,
        data_dir=_data_dir(),
        preferences_path=preferences_path,
        csv_files=_list_csv_files(csv_dir),
        data_files=_list_storage_files("data"),
        classifications=store.list_classifications(),
        schedules=store.list_schedules(),
        default_schedule_id=store.get_default_schedule_id(),
        preferences_schedule_id=store.get_preferences_schedule_id(),
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
    return render_template(
        "index.html",
        defaults=defaults,
        stored_schedules=store.list_schedules(),
    )


@bp.route("/solve", methods=["POST"])
def run_solve():
    shifts_file = request.files.get("shifts_file")
    shifts_server = request.form.get("shifts_file_server", "").strip()
    schedule_id_raw = request.form.get("schedule_id", "").strip()
    people_file = request.files.get("people_file")
    people_server = request.form.get("people_file_server", "").strip()

    schedule_id = None
    if schedule_id_raw:
        try:
            schedule_id = int(schedule_id_raw)
        except ValueError:
            schedule_id = None

    has_shifts_source = (shifts_file and shifts_file.filename) or shifts_server or schedule_id
    if not has_shifts_source:
        flash("Choose a shifts source: upload a CSV, pick a server file, or pick a stored schedule.", "danger")
        return redirect(url_for("main.index"))
    if not ((people_file and people_file.filename) or people_server):
        flash("A people CSV file is required (upload or server file).", "danger")
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
        # Load config
        config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config/config.yaml"))
        if alpha is not None:
            config["alpha"] = alpha

        # Resolve the shifts source: stored schedule > server file > upload
        if schedule_id:
            if store.get_schedule(schedule_id) is None:
                raise ValueError("The selected stored schedule was not found.")
            shifts = store.schedule_to_shift_objects(schedule_id, config)
            if not shifts:
                raise ValueError("The selected stored schedule has no shifts.")
        elif shifts_server:
            server_path = _schedule_file_path(shifts_server)
            if not server_path.exists():
                raise ValueError(f"Server file {shifts_server} was not found.")
            shifts = load_shifts(str(server_path), config)
        else:
            fd, shifts_path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "wb") as f:
                shifts_file.save(f)
            shifts = load_shifts(shifts_path, config)

        # Resolve the people source: server file > upload
        if people_server:
            people_csv_path = _schedule_file_path(people_server)
            if not people_csv_path.exists():
                raise ValueError(f"Server file {people_server} was not found.")
            people = load_people(str(people_csv_path))
        else:
            fd, people_path = tempfile.mkstemp(suffix=".csv")
            with os.fdopen(fd, "wb") as f:
                people_file.save(f)
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

        # Enrich results with institution and contact info
        person_map = {p.name: p for p in people}
        for r in results + pass2_results:
            person = person_map.get(r["person"])
            r["institution"] = person.institution if person else ""
            r["email"] = person.email if person else ""
            r["phone"] = person.phone if person else ""

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
        session["results_path"] = _save_session_data(
            results, constraints, config_summary, pass2_results, schedule_id=schedule_id
        )

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
    payload = _load_session_payload()
    if payload is None:
        flash("No results found. Please run the scheduler first.", "warning")
        return redirect(url_for("main.index"))

    data = payload["results"]
    source_schedule = None
    if payload.get("schedule_id"):
        source_schedule = store.get_schedule(payload["schedule_id"])
    stats = compute_stats(data, payload.get("constraints", {}))
    return render_template(
        "results.html",
        nav_active="results",
        assignments=data,
        stats=stats,
        config_summary=payload.get("config_summary", {}),
        has_pass2=bool(payload.get("pass2_results")),
        source_schedule=source_schedule,
        now_date=datetime.utcnow().strftime("%Y-%m-%d"),
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

    # When the solve ran from a stored schedule, persist the assignments there
    # too so the named calendar shows them.
    schedule_id = payload.get("schedule_id")
    if schedule_id and request.form.get("save_to_schedule") == "1":
        schedule = store.get_schedule(schedule_id)
        if schedule:
            store.save_assignments(schedule_id, payload["results"], payload["saved_by"])
            store.bulk_upsert_contacts(payload["results"])
            flash(f"Assignments saved to schedule '{schedule['name']}'.", "success")
            return redirect(url_for("main.calendar_view", schedule=schedule["name"]))

    return redirect(url_for("main.calendar_view", source="results", file=target_path.name))


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


def _load_calendar_rows():
    """Resolve the calendar data source from query params.

    Returns (entries, context) where context carries the selector state the
    template needs. Sources, in priority order: session preview, legacy file
    params (?source=results|schedule), then named schedules (default).
    """
    from app.calendar_data import build_entries

    context = {
        "source": "",
        "preview": None,
        "selected_schedule": None,
        "selected_file": "",
        "legacy_csv": "",
    }

    # Local-file preview staged by POST /calendar/preview
    if request.args.get("preview") == "1":
        staged = session.get("calendar_preview")
        if staged and Path(staged.get("path", "")).exists():
            with open(staged["path"], encoding="utf-8") as f:
                payload = json.load(f)
            context["preview"] = {"filename": staged.get("filename", "")}
            return build_entries(payload.get("rows", [])), context
        flash("The previewed file is no longer available.", "warning")

    source = request.args.get("source", "").strip()
    if source == "schedule":
        name = request.args.get("schedule", "").strip()
        context["source"] = "schedule"
        context["legacy_csv"] = name
        if name:
            try:
                path = _schedule_file_path(name)
                if not path.exists():
                    raise ValueError(f"Schedule file {name} was not found.")
                return build_entries(_load_schedule_csv(path)), context
            except ValueError as exc:
                flash(f"Could not load schedule CSV: {exc}", "danger")
        return {}, context
    if source == "results":
        name = request.args.get("file", "").strip()
        context["source"] = "results"
        context["selected_file"] = name
        if name:
            try:
                path = _result_file_path(name)
            except ValueError as exc:
                flash(str(exc), "danger")
                return {}, context
            payload = _load_saved_result_file(path)
            if payload is None:
                flash(f"Could not load saved results file {name}.", "danger")
                return {}, context
            return build_entries(payload["results"]), context
        return {}, context

    # Named-schedule mode (the default)
    schedule = None
    name = request.args.get("schedule", "").strip()
    if name:
        schedule = store.get_schedule_by_name(name)
        if schedule is None:
            flash(f"No schedule named '{name}' was found.", "warning")
    context["requested_tab"] = request.args.get("tab", "").strip()
    if schedule is None:
        schedule = _pick_default_schedule(context["requested_tab"])
    context["selected_schedule"] = schedule
    if schedule is None:
        return {}, context
    entries = build_entries(
        store.get_schedule_shifts(schedule["id"]),
        store.get_assignments(schedule["id"]),
    )
    return entries, context


def _pick_default_schedule(tab_slug: str):
    """Admin default schedule (if it fits the tab), else newest in the tab."""
    default_id = store.get_default_schedule_id()
    if default_id:
        schedule = store.get_schedule(default_id)
        if schedule and (not tab_slug or schedule.get("classification_slug") == tab_slug):
            return schedule
    classification = store.get_classification_by_slug(tab_slug) if tab_slug else None
    candidates = store.list_schedules(classification["id"] if classification else None)
    if not candidates and not tab_slug:
        return None
    return candidates[0] if candidates else None


@bp.route("/calendar")
def calendar_view():
    from app.calendar_data import day_agenda, month_grid, prev_next_anchors, week_grid

    view = request.args.get("view", "month").strip()
    if view not in {"month", "week", "today"}:
        view = "month"

    entries, context = _load_calendar_rows()

    anchor = _parse_anchor_date(request.args.get("date", ""))
    if anchor is None:
        # The month/year picker submits separate fields.
        try:
            year = int(request.args.get("year", ""))
            month = int(request.args.get("month", ""))
            anchor = datetime(year, month, 1).date()
        except ValueError:
            anchor = _default_anchor(entries)

    if view == "week":
        grid = week_grid(entries, anchor)
        agenda = None
    elif view == "today":
        grid = None
        agenda = day_agenda(entries, anchor)
    else:
        grid = month_grid(entries, anchor.year, anchor.month)
        agenda = None

    # Navigation URLs preserve the active source/tab/schedule selection.
    base_params = {"view": view}
    if context.get("source"):
        base_params["source"] = context["source"]
        if context.get("legacy_csv"):
            base_params["schedule"] = context["legacy_csv"]
        if context.get("selected_file"):
            base_params["file"] = context["selected_file"]
    elif context.get("preview"):
        base_params["preview"] = 1
    else:
        if context.get("requested_tab"):
            base_params["tab"] = context["requested_tab"]
        if context.get("selected_schedule"):
            base_params["schedule"] = context["selected_schedule"]["name"]

    prev_anchor, next_anchor = prev_next_anchors(view, anchor)
    nav = {
        "label": (grid or agenda)["label"],
        "date": anchor.isoformat(),
        "year": anchor.year,
        "month": anchor.month,
        "prev_url": url_for("main.calendar_view", date=prev_anchor, **base_params),
        "next_url": url_for("main.calendar_view", date=next_anchor, **base_params),
        "today_url": url_for("main.calendar_view", **base_params),
    }

    tabs = store.list_classifications(visible_only=True)
    selected = context.get("selected_schedule")
    active_tab = ""
    if context.get("source") or context.get("preview"):
        active_tab = "__files__"
    elif context.get("requested_tab"):
        active_tab = context["requested_tab"]
    elif selected and selected.get("classification_slug"):
        active_tab = selected["classification_slug"]
    elif tabs:
        active_tab = tabs[0]["slug"]

    active_classification = store.get_classification_by_slug(active_tab) if active_tab not in {"", "__files__"} else None
    tab_schedules = store.list_schedules(
        active_classification["id"] if active_classification else None
    )

    default_schedule_id = store.get_default_schedule_id()
    return render_template(
        "calendar.html",
        nav_active="calendar",
        tabs=tabs,
        active_tab=active_tab,
        view=view,
        nav=nav,
        grid=grid,
        agenda=agenda,
        preview=context.get("preview"),
        selected_schedule=selected,
        tab_schedules=tab_schedules,
        default_schedule_id=default_schedule_id,
        source=context.get("source", ""),
        legacy_csv=context.get("legacy_csv", ""),
        selected_file=context.get("selected_file", ""),
        result_files=_list_result_files(),
        schedule_files=_list_csv_files(_csv_dir()),
    )


@bp.route("/calendar/preview", methods=["POST"])
def calendar_preview():
    upload = request.files.get("preview_file")
    if not upload or not upload.filename:
        flash("Choose a CSV or JSON file to preview.", "danger")
        return redirect(url_for("main.calendar_view"))

    suffix = Path(secure_filename(upload.filename)).suffix.lower()
    if suffix not in {".csv", ".json"}:
        flash("Only .csv schedules or .json saved results can be previewed.", "danger")
        return redirect(url_for("main.calendar_view"))

    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            upload.save(f)
        if suffix == ".csv":
            rows = _read_block_csv_rows(Path(temp_path))
        else:
            payload = _load_saved_result_file(Path(temp_path))
            if payload is None:
                raise ValueError("Not a valid saved-results JSON file.")
            rows = payload["results"]
    except ValueError as exc:
        flash(f"Could not preview file: {exc}", "danger")
        return redirect(url_for("main.calendar_view"))
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    _clear_calendar_preview()
    fd, staged_path = tempfile.mkstemp(suffix=".json", prefix="mu2e_preview_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"rows": rows}, f)
    session["calendar_preview"] = {"path": staged_path, "filename": upload.filename}
    return redirect(url_for("main.calendar_view", preview=1))


def _clear_calendar_preview() -> None:
    staged = session.pop("calendar_preview", None)
    if staged and staged.get("path"):
        try:
            os.unlink(staged["path"])
        except OSError:
            pass


@bp.route("/calendar/preview/clear", methods=["POST"])
def calendar_preview_clear():
    _clear_calendar_preview()
    return redirect(url_for("main.calendar_view"))


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

        target_path = _schedule_file_path(original_name)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temp_path, target_path)
    except ValueError as exc:
        flash(f"Invalid schedule CSV: {exc}", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))
    except OSError as exc:
        flash(f"Could not save schedule CSV: {exc}", "danger")
        return redirect(url_for("main.calendar_view", source="schedule"))
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    flash(f"Uploaded schedule {original_name}.", "success")
    return redirect(url_for("main.calendar_view", source="schedule", schedule=target_path.name))


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
