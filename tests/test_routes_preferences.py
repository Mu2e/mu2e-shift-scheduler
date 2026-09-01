"""Tests for the admin-selected preference-collection schedule."""
from pathlib import Path

from app import store

SCHEDULE_ROWS = [
    {
        "shift_id": "pref-day-1", "shift_type": "Day", "days": "",
        "date": "2026-06-01", "date_end": "",
        "start_time": "08:00", "end_time": "16:00", "points": "1.0",
    },
    {
        "shift_id": "pref-night-1", "shift_type": "Night", "days": "",
        "date": "2026-06-01", "date_end": "",
        "start_time": "20:00", "end_time": "04:00", "points": "",
    },
]

LEGACY_CSV = (
    "shift_id,date,start_time,end_time\n"
    "legacy-1,2026-04-01,08:00,16:00\n"
)


def _write_legacy_csv(app):
    path = Path(app.config["PREFERENCES_SHIFTS_CSV"])
    path.write_text(LEGACY_CSV, encoding="utf-8")


def test_preferences_fall_back_to_legacy_csv(user_client, app):
    _write_legacy_csv(app)
    html = user_client.get("/preferences/").get_data(as_text=True)
    assert "legacy-1" in html
    assert "Collecting preferences for schedule" not in html


def test_admin_selects_preference_schedule(admin_client, user_client, app):
    _write_legacy_csv(app)
    with app.app_context():
        classifications = {c["name"]: c for c in store.list_classifications()}
        sid, _ = store.upsert_schedule(
            "Fall 2026", classifications["General Shifts"]["id"],
            SCHEDULE_ROWS, source="upload",
        )

    response = admin_client.post(
        "/admin/settings",
        data={"form_name": "preferences_schedule", "preferences_schedule_id": str(sid)},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert store.get_preferences_schedule_id() == sid

    # The submit page now lists exactly that schedule's shifts, with a banner
    html = user_client.get("/preferences/").get_data(as_text=True)
    assert "Collecting preferences for schedule" in html
    assert "Fall 2026" in html
    assert "pref-day-1" in html
    assert "pref-night-1" in html
    assert "legacy-1" not in html

    # Clearing the setting restores the legacy CSV
    response = admin_client.post(
        "/admin/settings",
        data={"form_name": "preferences_schedule", "preferences_schedule_id": ""},
        follow_redirects=False,
    )
    assert response.status_code == 302
    html = user_client.get("/preferences/").get_data(as_text=True)
    assert "legacy-1" in html


def test_deleted_preference_schedule_falls_back(admin_client, user_client, app):
    _write_legacy_csv(app)
    with app.app_context():
        sid, _ = store.upsert_schedule("Doomed", None, SCHEDULE_ROWS, source="upload")
        store.set_preferences_schedule_id(sid)
        store.delete_schedule(sid)
    html = user_client.get("/preferences/").get_data(as_text=True)
    assert "legacy-1" in html


def test_settings_rejects_bogus_schedule(admin_client, app):
    response = admin_client.post(
        "/admin/settings",
        data={"form_name": "preferences_schedule", "preferences_schedule_id": "9999"},
        follow_redirects=True,
    )
    assert "Choose a valid schedule" in response.get_data(as_text=True)
    with app.app_context():
        assert store.get_preferences_schedule_id() is None


def test_preference_submission_against_schedule(user_client, app):
    with app.app_context():
        sid, _ = store.upsert_schedule("Pref Sched", None, SCHEDULE_ROWS, source="upload")
        store.set_preferences_schedule_id(sid)
    response = user_client.post(
        "/preferences/submit",
        data={"name": "Alice", "pref[]": ["pref-day-1", "pref-night-1"]},
        follow_redirects=False,
    )
    assert response.status_code == 302
    import json
    payload = json.loads(Path(app.config["PREFERENCES_JSON"]).read_text())
    assert payload[0]["name"] == "Alice"
    assert payload[0]["preferences"] == ["pref-day-1", "pref-night-1"]
