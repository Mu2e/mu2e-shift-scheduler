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
