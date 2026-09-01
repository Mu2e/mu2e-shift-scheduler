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


SETTING_REMINDER_DAYS = "reminder_days_ahead"


@bp.route("/email-templates", methods=["GET", "POST"])
@admin_required
def email_templates():
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            if action == "create":
                store.create_email_template(request.form.get("name", ""))
                flash("Template created.", "success")
            elif action == "delete":
                store.delete_email_template(int(request.form.get("template_id", "0")))
                flash("Template deleted.", "success")
            else:
                classification_id = None
                raw = request.form.get("classification_id", "").strip()
                if raw:
                    classification_id = int(raw)
                store.update_email_template(
                    int(request.form.get("template_id", "0")),
                    subject=request.form.get("subject", ""),
                    body=request.form.get("body", ""),
                    classification_id=classification_id,
                )
                flash("Template saved.", "success")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("admin.email_templates"))

    from app.emailer import TEMPLATE_FIELDS

    return render_template(
        "email_templates.html",
        nav_active="admin",
        templates=store.list_email_templates(),
        classifications=store.list_classifications(),
        template_fields=TEMPLATE_FIELDS,
        smtp_configured=bool(current_app.config.get("SMTP_HOST")),
    )


@bp.route("/reminders", methods=["GET", "POST"])
@admin_required
def reminders():
    from app import emailer

    if request.method == "POST" and request.form.get("action") == "send":
        try:
            window_days = int(request.form.get("days", "7"))
        except ValueError:
            window_days = 7
        window = store.upcoming_assignments(window_days)
        sent = 0
        failures = []
        for key in request.form.getlist("selected[]"):
            schedule_id, _, shift_id = key.partition("|")
            try:
                schedule_id = int(schedule_id)
            except ValueError:
                continue
            shift = next(
                (row for row in window
                 if row["schedule_id"] == schedule_id and row["shift_id"] == shift_id),
                None,
            )
            if shift is None:
                failures.append(f"{shift_id}: no longer in the reminder window")
                continue
            template = store.template_for_classification(shift.get("classification_id"))
            if template is None:
                failures.append(f"{shift_id}: no email template defined")
                continue
            try:
                emailer.send_reminder(shift, shift["person"], template,
                                      sent_by=getattr(current_user, "email", ""))
                sent += 1
            except ValueError as exc:
                failures.append(str(exc))
        if sent:
            flash(f"Sent {sent} reminder email(s).", "success")
        for failure in failures[:10]:
            flash(failure, "danger")
        if len(failures) > 10:
            flash(f"...and {len(failures) - 10} more failures.", "danger")
        return redirect(url_for("admin.reminders", days=request.form.get("days", "7")))

    # GET: preview the reminder list for the configured window
    try:
        days = int(request.args.get("days", "") or
                   store.get_setting(SETTING_REMINDER_DAYS, "7"))
    except ValueError:
        days = 7
    days = max(0, min(days, 365))
    if request.args.get("days"):
        store.set_setting(SETTING_REMINDER_DAYS, str(days))

    rows = []
    for shift in store.upcoming_assignments(days):
        template = store.template_for_classification(shift.get("classification_id"))
        rows.append({
            **shift,
            "email": store.resolve_reminder_email(shift["person"]),
            "template_name": template["name"] if template else "",
        })
    return render_template(
        "reminders.html",
        nav_active="admin",
        days=days,
        rows=rows,
        email_log=store.recent_email_log(25),
        smtp_configured=bool(current_app.config.get("SMTP_HOST")),
    )


@bp.route("/reminders/send-one", methods=["POST"])
@admin_required
def reminders_send_one():
    """Send a single reminder from the calendar context menu."""
    from app import emailer

    return_params = {
        "schedule": request.form.get("schedule") or None,
        "tab": request.form.get("tab") or None,
        "view": request.form.get("view") or None,
        "date": request.form.get("date") or None,
    }
    try:
        schedule_id = int(request.form.get("schedule_id", ""))
    except ValueError:
        flash("Choose a stored schedule first.", "danger")
        return redirect(url_for("main.calendar_view"))
    schedule = store.get_schedule(schedule_id)
    shift_id = request.form.get("shift_id", "").strip()
    if schedule is None:
        flash("Schedule not found.", "danger")
        return redirect(url_for("main.calendar_view"))

    shift = next(
        (row for row in store.get_schedule_shifts(schedule_id) if row["shift_id"] == shift_id),
        None,
    )
    assignment = store.get_assignments(schedule_id).get(shift_id)
    person = (assignment or {}).get("person", "").strip()
    if shift is None or not person or person.upper() == "UNASSIGNED":
        flash("That shift has no assigned person to remind.", "danger")
        return redirect(url_for("main.calendar_view", **return_params))

    shift = dict(shift)
    shift["schedule_name"] = schedule["name"]
    shift["classification_name"] = schedule.get("classification_name", "")
    template = store.template_for_classification(schedule.get("classification_id"))
    if template is None:
        flash("No email template is defined. Create one under Admin → Email Templates.", "danger")
        return redirect(url_for("main.calendar_view", **return_params))
    try:
        recipient = emailer.send_reminder(shift, person, template,
                                          sent_by=getattr(current_user, "email", ""))
        flash(f"Reminder sent to {person} <{recipient}>.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("main.calendar_view", **return_params))


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
