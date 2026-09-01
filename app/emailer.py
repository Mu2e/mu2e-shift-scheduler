"""
Shift reminder email rendering and SMTP delivery.

SMTP settings come from the app config (SMTP_HOST, SMTP_PORT, SMTP_FROM,
SMTP_STARTTLS; see .env.example / the Helm values). An empty SMTP_HOST
disables sending with a clear error, so development instances fail safe.

Templates use Python str.format placeholders; unknown fields render as
empty strings so a template edit can never crash a send.
"""
import smtplib
from email.message import EmailMessage

from flask import current_app


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


TEMPLATE_FIELDS = [
    "name", "schedule", "classification", "shift_name", "shift_id",
    "date", "date_end", "date_end_suffix", "start_time", "end_time", "days",
]


def render_template(text: str, context: dict) -> str:
    return str(text or "").format_map(_SafeDict(context))


def reminder_context(shift: dict, person: str) -> dict:
    """Template context for one assigned shift row (store.upcoming_assignments
    shape, or schedule_shifts row plus schedule metadata)."""
    date_end = str(shift.get("date_end", "") or "")
    if date_end and date_end != shift.get("date"):
        date_end_suffix = f" through {date_end}"
    else:
        date_end = ""
        date_end_suffix = ""
    return {
        "name": person,
        "schedule": shift.get("schedule_name", ""),
        "classification": shift.get("classification_name", "") or "",
        "shift_name": shift.get("shift_name") or shift.get("shift_id", ""),
        "shift_id": shift.get("shift_id", ""),
        "date": shift.get("date", ""),
        "date_end": date_end,
        "date_end_suffix": date_end_suffix,
        "start_time": shift.get("start_time", ""),
        "end_time": shift.get("end_time", ""),
        "days": shift.get("days", ""),
    }


def send_email(recipient: str, subject: str, body: str) -> None:
    """Deliver one plain-text message; raises ValueError/OSError on failure."""
    host = current_app.config.get("SMTP_HOST", "")
    if not host:
        raise ValueError(
            "Email is not configured on this instance (SMTP_HOST is unset)."
        )
    if not recipient or "@" not in recipient:
        raise ValueError(f"'{recipient or '(empty)'}' is not a valid email address.")

    message = EmailMessage()
    message["From"] = current_app.config.get("SMTP_FROM", "mu2e-shifts@fnal.gov")
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    port = int(current_app.config.get("SMTP_PORT", 25))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if current_app.config.get("SMTP_STARTTLS"):
            smtp.starttls()
        smtp.send_message(message)


def send_reminder(shift: dict, person: str, template: dict, sent_by: str) -> str:
    """Render and send one reminder; logs the outcome. Returns the recipient
    address. Raises ValueError with a user-readable message on failure."""
    from app import store

    recipient = store.resolve_reminder_email(person)
    if not recipient:
        raise ValueError(
            f"No email address is known for {person}. They can set one by "
            "logging in, or an admin can add it on the Users page."
        )
    context = reminder_context(shift, person)
    subject = render_template(template.get("subject", ""), context)
    body = render_template(template.get("body", ""), context)
    try:
        send_email(recipient, subject, body)
    except (ValueError, OSError, smtplib.SMTPException) as exc:
        store.log_email(recipient, person, subject, shift.get("schedule_id"),
                        shift.get("shift_id", ""), sent_by, status=f"failed: {exc}")
        raise ValueError(f"Could not send to {recipient}: {exc}")
    store.log_email(recipient, person, subject, shift.get("schedule_id"),
                    shift.get("shift_id", ""), sent_by)
    return recipient
