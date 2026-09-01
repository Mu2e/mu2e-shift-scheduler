"""Tests for reminder emails: templates, calendar send, batch page."""
from datetime import date, timedelta

import pytest

from app import store


class FakeSMTP:
    """Captures messages instead of talking to a mail server."""

    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("connection refused")
        FakeSMTP.sent.append(message)


@pytest.fixture()
def smtp(app, monkeypatch):
    import app.emailer as emailer_module

    FakeSMTP.sent = []
    FakeSMTP.fail = False
    monkeypatch.setattr(emailer_module.smtplib, "SMTP", FakeSMTP)
    app.config["SMTP_HOST"] = "smtp.example.gov"
    return FakeSMTP


def _upcoming_schedule(app, classification="Run Coordinators", person="Regular Shifter",
                       name="Fall 2026"):
    start = date.today() + timedelta(days=2)
    end = start + timedelta(days=3)
    rows = [{
        "shift_id": "rc-w1", "shift_type": "Run Coordinator", "days": "",
        "date": start.isoformat(), "date_end": end.isoformat(),
        "start_time": "08:00", "end_time": "17:00", "points": "3.0",
    }]
    with app.app_context():
        classifications = {c["name"]: c for c in store.list_classifications()}
        sid, _ = store.upsert_schedule(name, classifications[classification]["id"],
                                       rows, source="upload")
        store.save_assignments(sid, [{"shift_id": "rc-w1", "person": person}], "t@t.gov")
    return sid


def test_default_templates_seeded(app):
    with app.app_context():
        templates = {t["name"]: t for t in store.list_email_templates()}
    assert set(templates) == {"General Shifts", "Run Coordinators", "Oncall DAQ Experts"}
    assert "{name}" in templates["General Shifts"]["body"]
    assert templates["Run Coordinators"]["classification_id"] is not None


def test_template_editing(admin_client, app):
    with app.app_context():
        template = store.list_email_templates()[0]
    response = admin_client.post(
        "/admin/email-templates",
        data={"action": "save", "template_id": str(template["id"]),
              "subject": "New subject {shift_name}", "body": "Hi {name}",
              "classification_id": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert store.get_email_template(template["id"])["subject"] == "New subject {shift_name}"

    # Create + delete
    admin_client.post("/admin/email-templates", data={"action": "create", "name": "Extra"})
    with app.app_context():
        extra = next(t for t in store.list_email_templates() if t["name"] == "Extra")
    admin_client.post("/admin/email-templates",
                      data={"action": "delete", "template_id": str(extra["id"])})
    with app.app_context():
        assert all(t["name"] != "Extra" for t in store.list_email_templates())


def test_send_one_from_calendar(admin_client, user_client, app, smtp):
    # The assigned person is the registered user fixture (has an account email)
    sid = _upcoming_schedule(app)
    response = admin_client.post(
        "/admin/reminders/send-one",
        data={"schedule_id": str(sid), "shift_id": "rc-w1", "schedule": "Fall 2026"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert "Reminder sent to Regular Shifter" in html
    assert len(smtp.sent) == 1
    message = smtp.sent[0]
    assert message["To"] == "shifter@example.gov"  # registered account address
    assert "run coordinator" in message["Subject"].lower() or "Run Coordinator" in message.get_content()
    assert "Regular Shifter" in message.get_content()
    assert "Fall 2026" in message.get_content()
    with app.app_context():
        log = store.recent_email_log()
        assert log[0]["status"] == "sent"
        assert log[0]["recipient"] == "shifter@example.gov"


def test_send_one_requires_assignment_and_admin(admin_client, user_client, app, smtp):
    sid = _upcoming_schedule(app, person="")
    response = admin_client.post(
        "/admin/reminders/send-one",
        data={"schedule_id": str(sid), "shift_id": "rc-w1"},
        follow_redirects=True,
    )
    assert "no assigned person" in response.get_data(as_text=True)
    assert smtp.sent == []

    assert user_client.post(
        "/admin/reminders/send-one",
        data={"schedule_id": str(sid), "shift_id": "rc-w1"},
        follow_redirects=False,
    ).status_code == 302  # admin_required bounce


def test_reminders_page_lists_window(admin_client, user_client, app, smtp):
    _upcoming_schedule(app)  # starts in 2 days
    html = admin_client.get("/admin/reminders?days=7").get_data(as_text=True)
    assert "Run Coordinator" in html
    assert "shifter@example.gov" in html
    # Narrow window excludes it
    html = admin_client.get("/admin/reminders?days=1").get_data(as_text=True)
    assert "No assigned shifts start in this window" in html
    # The window is remembered
    with app.app_context():
        assert store.get_setting("reminder_days_ahead") == "1"


def test_batch_send(admin_client, user_client, app, smtp):
    sid = _upcoming_schedule(app)
    with app.app_context():
        store.upsert_contact("No Account Person", institution="X")  # no email anywhere
        store.save_assignments(sid, [{"shift_id": "rc-w1", "person": "Regular Shifter"}], "t")
    response = admin_client.post(
        "/admin/reminders",
        data={"action": "send", "days": "7", "selected[]": [f"{sid}|rc-w1"]},
        follow_redirects=True,
    )
    assert "Sent 1 reminder email(s)" in response.get_data(as_text=True)
    assert len(smtp.sent) == 1


def test_send_failure_is_logged(admin_client, user_client, app, smtp):
    sid = _upcoming_schedule(app)
    smtp.fail = True
    response = admin_client.post(
        "/admin/reminders/send-one",
        data={"schedule_id": str(sid), "shift_id": "rc-w1"},
        follow_redirects=True,
    )
    assert "Could not send" in response.get_data(as_text=True)
    with app.app_context():
        assert store.recent_email_log()[0]["status"].startswith("failed")


def test_unconfigured_smtp_gives_clear_error(admin_client, user_client, app):
    sid = _upcoming_schedule(app)
    app.config["SMTP_HOST"] = ""
    response = admin_client.post(
        "/admin/reminders/send-one",
        data={"schedule_id": str(sid), "shift_id": "rc-w1"},
        follow_redirects=True,
    )
    assert "not configured" in response.get_data(as_text=True)


def test_calendar_context_menu_includes_email_item(admin_client, app, smtp):
    _upcoming_schedule(app)
    html = admin_client.get("/calendar?schedule=Fall+2026").get_data(as_text=True)
    assert "assignMenuEmail" in html
    assert "reminderForm" in html
