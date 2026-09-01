"""
Admin blueprint: shift-schedule setup (generator), classification taxonomy,
and calendar settings. All routes require an administrator.
"""
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from app import store
from app.auth import admin_required
import re

from scheduler.blocks import (
    REPETITIONS,
    BlockSpec,
    ShiftSpec,
    generate_schedule_rows,
    rows_to_block_csv_string,
    slugify,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _parse_date(field: str, label: str):
    raw = request.form.get(field, "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{label}: enter a date as YYYY-MM-DD.")


def _parse_time(raw: str, label: str) -> str:
    raw = (raw or "").strip()
    try:
        datetime.strptime(raw, "%H:%M")
    except ValueError:
        raise ValueError(f"{label}: enter a time as HH:MM (24-hour).")
    return raw


def _parse_shift_setup_form(form):
    """Parse the Shift Setup form into (name, classification_id, start, end, specs)."""
    name = form.get("name", "").strip()
    if not name:
        raise ValueError("Enter a schedule name.")

    classification_id = None
    raw_classification = form.get("classification", "").strip()
    if raw_classification:
        classification = store.get_classification_by_slug(raw_classification)
        if classification is None:
            raise ValueError("Choose a valid classification.")
        classification_id = classification["id"]

    start_date = _parse_date("date_start", "Start date")
    end_date = _parse_date("date_end", "Stop date")
    if end_date < start_date:
        raise ValueError("Stop date must be on or after the start date.")

    repetition = form.get("repeat", "week").strip()
    if repetition not in REPETITIONS:
        raise ValueError("Choose a valid repetition rate.")

    # Blocks use indexed field names (block_name_0, block_length_0,
    # block_days_0[]) because each row carries its own checkbox group.
    block_indices = sorted({
        int(match.group(1))
        for key in form
        for match in [re.fullmatch(r"block_name_(\d+)", key)]
        if match
    })
    blocks = []
    for i in block_indices:
        block_name = form.get(f"block_name_{i}", "").strip()
        length_raw = form.get(f"block_length_{i}", "").strip()
        weekdays = []
        for value in form.getlist(f"block_days_{i}[]"):
            try:
                day = int(value)
            except ValueError:
                continue
            if 0 <= day <= 6 and day not in weekdays:
                weekdays.append(day)
        if not block_name and not weekdays and not length_raw:
            continue  # blank filler row
        if not block_name:
            raise ValueError(f"Block {len(blocks) + 1}: enter a block name.")
        try:
            length_days = int(length_raw or "1")
        except ValueError:
            raise ValueError(f"Block '{block_name}': length must be a whole number of days.")
        if length_days < 1:
            raise ValueError(f"Block '{block_name}': length must be at least 1 day.")
        if weekdays and length_days != len(weekdays):
            # Checked weekdays define the block; keep the length consistent.
            length_days = len(weekdays)
        blocks.append(BlockSpec(name=block_name, length_days=length_days,
                                weekdays=sorted(weekdays)))
    if not blocks:
        raise ValueError("Define at least one block (name and days).")
    if repetition == "day" and any(b.weekdays for b in blocks):
        raise ValueError(
            "Weekday selections require weekly, 2-week, or monthly repetition."
        )

    names = form.getlist("shift_name[]")
    starts = form.getlist("shift_start[]")
    ends = form.getlist("shift_end[]")
    weights = form.getlist("shift_weight[]")

    shifts = []
    for index, shift_name in enumerate(names):
        shift_name = (shift_name or "").strip()
        start_time = (starts[index] if index < len(starts) else "").strip()
        end_time = (ends[index] if index < len(ends) else "").strip()
        weight_raw = (weights[index] if index < len(weights) else "").strip()
        if not shift_name and not start_time and not end_time:
            continue  # blank filler row (no-JS fallback rows)
        if not shift_name:
            raise ValueError(f"Shift {index + 1}: enter a shift name.")
        start_time = _parse_time(start_time, f"Shift '{shift_name}' start time")
        end_time = _parse_time(end_time, f"Shift '{shift_name}' stop time")
        weight = None
        if weight_raw:
            try:
                weight = float(weight_raw)
            except ValueError:
                raise ValueError(f"Shift '{shift_name}': weight must be a number.")
            if weight < 0:
                raise ValueError(f"Shift '{shift_name}': weight cannot be negative.")
        shifts.append(ShiftSpec(
            name=shift_name,
            start_time=start_time,
            end_time=end_time,
            weight=weight,
        ))

    if not shifts:
        raise ValueError("Define at least one shift (name, start time, stop time).")
    return name, classification_id, start_date, end_date, blocks, shifts, repetition


def _write_backing_csv(schedule_name: str, rows: list[dict]) -> str:
    """Write/refresh the block CSV in CSV_DIR that mirrors a stored schedule."""
    from app.routes import _csv_dir

    filename = f"{slugify(schedule_name)}.csv"
    path = _csv_dir() / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rows_to_block_csv_string(rows), encoding="utf-8")
    return filename


@bp.route("/shift-setup")
@admin_required
def shift_setup():
    return render_template(
        "shift_setup.html",
        nav_active="admin",
        classifications=store.list_classifications(),
        form_data={},
    )


@bp.route("/shift-setup/generate", methods=["POST"])
@admin_required
def shift_setup_generate():
    try:
        (name, classification_id, start_date, end_date,
         blocks, shifts, repetition) = _parse_shift_setup_form(request.form)
        rows, skipped = generate_schedule_rows(
            name, start_date, end_date, blocks, shifts, repetition
        )
        if not rows:
            raise ValueError(
                "No complete shift occurrences fit inside the date range. "
                "Check the dates, repetition rate, and weekday selection."
            )
    except ValueError as exc:
        flash(str(exc), "danger")
        return render_template(
            "shift_setup.html",
            nav_active="admin",
            classifications=store.list_classifications(),
            form_data=request.form,
        ), 400

    existing = store.get_schedule_by_name(name)
    if existing and request.form.get("overwrite") != "1":
        return render_template(
            "schedule_confirm_overwrite.html",
            nav_active="admin",
            existing=existing,
            action=url_for("admin.shift_setup_generate"),
            form_fields={
                key: request.form.getlist(key)
                for key in request.form
                if key != "overwrite"
            },
        )

    schedule_id, dropped = store.upsert_schedule(
        name,
        classification_id,
        rows,
        source="generated",
        created_by=getattr(current_user, "email", ""),
        csv_filename=_write_backing_csv(name, rows),
    )
    message = f"Generated schedule '{name}' with {len(rows)} shifts."
    if skipped:
        message += f" Skipped {skipped} partial occurrence(s) outside the date range."
    if dropped:
        message += f" Dropped {dropped} saved assignment(s) for removed shifts."
    flash(message, "success")

    schedule = store.get_schedule(schedule_id)
    return redirect(url_for(
        "main.calendar_view",
        schedule=name,
        tab=schedule.get("classification_slug") or None,
        date=start_date.isoformat(),
    ))


@bp.route("/taxonomy/add", methods=["POST"])
@admin_required
def taxonomy_add():
    try:
        store.create_classification(request.form.get("name", ""))
        flash("Classification added.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("main.configuration"))


@bp.route("/taxonomy/<int:cid>", methods=["POST"])
@admin_required
def taxonomy_update(cid: int):
    action = request.form.get("action", "")
    try:
        if action == "delete":
            store.delete_classification(cid)
            flash("Classification deleted.", "success")
        elif action == "rename":
            store.update_classification(cid, name=request.form.get("name", ""))
            flash("Classification renamed.", "success")
        elif action in {"show", "hide"}:
            store.update_classification(cid, show_tab=(action == "show"))
            flash("Calendar tab visibility updated.", "success")
        else:
            flash("Unknown action.", "danger")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("main.configuration"))


@bp.route("/settings", methods=["POST"])
@admin_required
def settings():
    # Visible calendar tabs: one checkbox per classification.
    if request.form.get("form_name") == "tabs":
        visible = set(request.form.getlist("visible[]"))
        for classification in store.list_classifications():
            store.update_classification(
                classification["id"],
                show_tab=(str(classification["id"]) in visible),
            )
        flash("Visible calendar tabs updated.", "success")
    elif request.form.get("form_name") == "default_schedule":
        raw = request.form.get("default_schedule_id", "").strip()
        if raw:
            try:
                schedule_id = int(raw)
            except ValueError:
                schedule_id = None
            if schedule_id and store.get_schedule(schedule_id):
                store.set_default_schedule_id(schedule_id)
                flash("Default calendar updated.", "success")
            else:
                flash("Choose a valid schedule.", "danger")
        else:
            store.set_default_schedule_id(None)
            flash("Default calendar cleared.", "success")
    elif request.form.get("form_name") == "preferences_schedule":
        raw = request.form.get("preferences_schedule_id", "").strip()
        if raw:
            try:
                schedule_id = int(raw)
            except ValueError:
                schedule_id = None
            if schedule_id and store.get_schedule(schedule_id):
                store.set_preferences_schedule_id(schedule_id)
                schedule = store.get_schedule(schedule_id)
                flash(f"Preferences are now collected for schedule '{schedule['name']}'.", "success")
            else:
                flash("Choose a valid schedule.", "danger")
        else:
            store.set_preferences_schedule_id(None)
            flash("Preference schedule cleared — the legacy preference shifts CSV will be used.", "success")
    else:
        flash("Unknown settings form.", "danger")
    return redirect(url_for("main.configuration"))
