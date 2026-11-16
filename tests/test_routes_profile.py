"""Tests for user contact info: profile page, admin editing, popover fallback."""
import sqlite3

from app import store
from tests.conftest import ADMIN_EMAIL


def _user_row(app, email):
    with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def test_profile_page_renders(user_client):
    html = user_client.get("/profile").get_data(as_text=True)
    assert "My Profile" in html
    assert "shifter@example.gov" in html


def test_user_edits_own_contact_info(user_client, app):
    response = user_client.post(
        "/profile",
        data={"phone": "+1 630 555 1234", "institution": "Argonne"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    row = _user_row(app, "shifter@example.gov")
    assert row["phone"] == "+1 630 555 1234"
    assert row["institution"] == "Argonne"

    # Synced into the contacts table -> calendar popovers see it
    with app.app_context():
        contact = store.get_contact("Regular Shifter")
        assert contact["phone"] == "+1 630 555 1234"
        assert contact["institution"] == "Argonne"
        assert contact["email"] == "shifter@example.gov"

    # Clearing a field is authoritative for one's own profile
    user_client.post("/profile", data={"phone": "", "institution": "Argonne"})
    with app.app_context():
        assert store.get_contact("Regular Shifter")["phone"] == ""


def test_admin_edits_user_contact_info(admin_client, user_client, app):
    row = _user_row(app, "shifter@example.gov")
    response = admin_client.post(
        f"/admin/users/{row['id']}/contact",
        data={"phone": "+1 555 000 1111", "institution": "Fermilab"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    row = _user_row(app, "shifter@example.gov")
    assert row["phone"] == "+1 555 000 1111"
    assert row["institution"] == "Fermilab"

    html = admin_client.get("/admin/users").get_data(as_text=True)
    assert "+1 555 000 1111" in html
    assert "Fermilab" in html


def test_contact_edit_requires_admin(user_client, app):
    row = _user_row(app, ADMIN_EMAIL)
    response = user_client.post(
        f"/admin/users/{row['id']}/contact",
        data={"phone": "hax", "institution": "hax"},
        follow_redirects=False,
    )
    assert response.status_code == 302  # bounced by admin_required
    row = _user_row(app, ADMIN_EMAIL)
    assert row["phone"] == ""


def test_users_table_fallback_supplies_phone_and_institution(user_client, app):
    user_client.post("/profile", data={"phone": "x2001", "institution": "FNAL"})
    # Remove the synced contacts row to force the users-table fallback path
    with app.app_context():
        with store.connect() as conn:
            conn.execute("DELETE FROM contacts")
        contact = store.get_contact("Regular Shifter")
        assert contact["source"] == "users"
        assert contact["phone"] == "x2001"
        assert contact["institution"] == "FNAL"


def test_profile_requires_login(client):
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_nav_has_my_profile_link(user_client):
    html = user_client.get("/about").get_data(as_text=True)
    assert "My Profile (Regular Shifter)" in html
    assert 'href="/profile"' in html


def test_incomplete_profile_banner(user_client):
    # Fresh account: no phone/institution -> banner on other pages, not on /profile
    assert 'id="profile-incomplete"' in user_client.get("/about").get_data(as_text=True)
    assert 'id="profile-incomplete"' not in user_client.get("/profile").get_data(as_text=True)

    user_client.post("/profile", data={"phone": "x2001", "institution": "FNAL"})
    assert 'id="profile-incomplete"' not in user_client.get("/about").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Extended profile: contact email override, extra fields, display-name rename
# ---------------------------------------------------------------------------

import json

from app import auth


def _seed_schedule_with(app, person):
    with app.app_context():
        cid = store.create_classification("Profile Test")
        sid, _ = store.upsert_schedule(
            "Profile Sched", cid,
            [{"shift_id": "p1", "shift_type": "Day", "days": "Mon", "date": "2026-12-07",
              "date_end": "2026-12-07", "start_time": "08:00", "end_time": "16:00", "points": "1"}],
            source="upload",
        )
        store.save_assignments(sid, [{"shift_id": "p1", "person": person, "points": 1}], saved_by="t")
    return sid


def test_extended_fields_saved_and_synced(user_client, app):
    response = user_client.post("/profile", data={
        "contact_email": "me@home.example",
        "mobile_phone": "+1 312 555 0101",
        "alt_contact_name": "Pat Backup",
        "alt_contact_phone": "+1 312 555 0202",
        "chat_handle": "@shifter",
        "timezone": "America/Chicago",
    })
    assert response.status_code == 302
    row = _user_row(app, "shifter@example.gov")
    assert row["contact_email"] == "me@home.example"
    assert row["timezone"] == "America/Chicago"
    with app.app_context():
        contact = store.get_contact("Regular Shifter")
        assert contact["email"] == "me@home.example"  # override wins over login email
        assert contact["mobile_phone"] == "+1 312 555 0101"
        assert contact["alt_contact_name"] == "Pat Backup"
        assert contact["chat_handle"] == "@shifter"
        assert store.resolve_reminder_email("Regular Shifter") == "me@home.example"


def test_profile_validation_rejects_bad_input(user_client, app):
    for data in ({"contact_email": "not-an-email"},
                 {"timezone": "Mars/Olympus_Mons"},
                 {"mobile_phone": "call me maybe"},
                 {"chat_handle": "x" * 101}):
        response = user_client.post("/profile", data=data)
        assert response.status_code == 400, data
    row = _user_row(app, "shifter@example.gov")
    assert row["contact_email"] == "" and row["timezone"] == "" and row["mobile_phone"] == ""


def test_rename_updates_assignments_contacts_and_preferences(user_client, app):
    sid = _seed_schedule_with(app, "Regular Shifter")
    prefs_path = app.config.get("PREFERENCES_JSON")
    with open(prefs_path, "w") as f:
        json.dump([{"name": "regular shifter", "preferences": ["p1"], "schedule": "",
                    "submitted_at": "2026-11-01T00:00:00"},
                   {"name": "Someone Else", "preferences": ["p1"], "schedule": "",
                    "submitted_at": "2026-11-01T00:00:00"}], f)
    user_client.post("/profile", data={"phone": "x1"})  # creates contacts row

    response = user_client.post("/profile", data={"display_name": "Reg Shifter", "phone": "x1"})
    assert response.status_code == 302

    assert _user_row(app, "shifter@example.gov")["name"] == "Reg Shifter"
    assert _user_row(app, "shifter@example.gov")["name_custom"] == 1
    with app.app_context():
        assert store.get_assignments(sid)["p1"]["person"] == "Reg Shifter"
        assert store.get_contact("Reg Shifter")["phone"] == "x1"
        with store.connect() as conn:
            assert conn.execute("SELECT 1 FROM contacts WHERE name = 'Regular Shifter'").fetchone() is None
    with open(prefs_path) as f:
        names = [s["name"] for s in json.load(f)]
    assert names == ["Reg Shifter", "Someone Else"]
    assert "My Profile (Reg Shifter)" in user_client.get("/about").get_data(as_text=True)


def test_rename_refused_when_name_taken_by_another_account(user_client, app):
    sid = _seed_schedule_with(app, "Regular Shifter")
    response = user_client.post("/profile", data={"display_name": "MU2E-ADMIN"})
    assert response.status_code == 400
    assert "already used by another account" in response.get_data(as_text=True)
    assert _user_row(app, "shifter@example.gov")["name"] == "Regular Shifter"
    with app.app_context():
        assert store.get_assignments(sid)["p1"]["person"] == "Regular Shifter"


def test_rename_refused_onto_foreign_contact(user_client, app):
    with app.app_context():
        store.upsert_contact("Jane Doe", email="jane@example.gov", phone="555")
    response = user_client.post("/profile", data={"display_name": "Jane Doe"})
    assert response.status_code == 400
    with app.app_context():
        assert store.get_contact("Jane Doe")["phone"] == "555"


def test_rename_merges_into_contact_with_own_email(user_client, app):
    # A CSV import stored this person under another spelling with their email.
    with app.app_context():
        store.upsert_contact("R. Shifter", email="shifter@example.gov", phone="old")
    sid = _seed_schedule_with(app, "Regular Shifter")
    response = user_client.post("/profile", data={"display_name": "R. Shifter", "phone": "x2002"})
    assert response.status_code == 302
    with app.app_context():
        assert store.get_assignments(sid)["p1"]["person"] == "R. Shifter"
        assert store.get_contact("R. Shifter")["phone"] == "x2002"


def test_custom_name_survives_sso_login_and_blank_reverts(user_client, app):
    claims = {"sub": "sub-1", "email": "shifter@example.gov", "name": "Regular Shifter"}
    with app.app_context():
        auth.upsert_oidc_user(claims)  # records sso_name
    sid = _seed_schedule_with(app, "Regular Shifter")
    user_client.post("/profile", data={"display_name": "Reggie"})
    with app.app_context():
        user = auth.upsert_oidc_user(claims)  # next SSO login
        assert user.name == "Reggie" and user.sso_name == "Regular Shifter"

    user_client.post("/profile", data={"display_name": ""})  # revert to SSO name
    row = _user_row(app, "shifter@example.gov")
    assert row["name"] == "Regular Shifter" and row["name_custom"] == 0
    with app.app_context():
        assert store.get_assignments(sid)["p1"]["person"] == "Regular Shifter"


def test_calendar_popover_carries_extended_fields(user_client, app):
    sid = _seed_schedule_with(app, "Regular Shifter")
    user_client.post("/profile", data={"mobile_phone": "+1 555", "timezone": "Europe/Rome",
                                       "chat_handle": "@rs"})
    with app.app_context():
        schedule = store.get_schedule(sid)
    html = user_client.get(f"/calendar?schedule={schedule['slug']}&view=month&date=2026-12-07").get_data(as_text=True)
    if 'data-mobile="+1 555"' not in html:  # calendar route may select schedules differently
        html = user_client.get(f"/calendar/{schedule['slug']}?date=2026-12-07").get_data(as_text=True)
    assert 'data-mobile="+1 555"' in html
    assert 'data-timezone="Europe/Rome"' in html
