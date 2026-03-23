"""
Flask routes for the Mu2e Shift Scheduler web interface.
"""
import io
import json
import os
import tempfile
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
    path = session.get("results_path")
    if not path or not Path(path).exists():
        return None, None, None, None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return (
        payload["results"],
        payload["constraints"],
        payload.get("config_summary", {}),
        payload.get("pass2_results", []),
    )


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/")
def welcome():
    return render_template("welcome.html")


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/schedule")
def index():
    # Pre-populate form with current config defaults if available
    config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config.yaml"))
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
        config = load_config(current_app.config.get("SCHEDULER_CONFIG", "config.yaml"))
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
