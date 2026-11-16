"""Admin role preview: view the site as a lower role without changing the account."""
import sqlite3

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def _role(app, email):
    with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
        return conn.execute("SELECT role FROM users WHERE email = ?", (email,)).fetchone()[0]


def test_admin_menu_offers_preview(admin_client):
    html = admin_client.get("/about").get_data(as_text=True)
    assert 'id="preview-as-user"' in html
    assert 'id="role-preview-banner"' not in html


def test_preview_hides_admin_and_blocks_admin_pages(admin_client, app):
    response = admin_client.post("/admin/preview", data={"role": "user"})
    assert response.status_code == 302

    html = admin_client.get("/about").get_data(as_text=True)
    assert 'id="role-preview-banner"' in html
    assert "Shifter (user)" in html
    assert 'id="preview-as-user"' not in html  # Admin menu gone

    response = admin_client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 302  # admin_required bounces the preview
    assert "/admin/users" not in response.headers["Location"]
    assert admin_client.get("/admin/api-tokens", follow_redirects=False).status_code == 302

    # The stored role is untouched.
    assert _role(app, ADMIN_EMAIL) == "admin"


def test_exit_preview_restores_admin(admin_client):
    admin_client.post("/admin/preview", data={"role": "user"})
    response = admin_client.post("/preview/exit", data={"next": "/about"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/about")
    html = admin_client.get("/about").get_data(as_text=True)
    assert 'id="role-preview-banner"' not in html
    assert admin_client.get("/admin/users").status_code == 200


def test_exit_preview_rejects_offsite_next(admin_client):
    admin_client.post("/admin/preview", data={"role": "user"})
    response = admin_client.post("/preview/exit", data={"next": "//evil.example/x"})
    assert "evil.example" not in response.headers["Location"]


def test_invalid_preview_roles_rejected(admin_client):
    for role in ("admin", "superuser", ""):
        admin_client.post("/admin/preview", data={"role": role})
        assert admin_client.get("/admin/users").status_code == 200, role


def test_non_admin_cannot_start_preview(user_client):
    response = user_client.post("/admin/preview", data={"role": "user"})
    assert response.status_code == 302
    with user_client.session_transaction() as sess:
        assert "preview_role" not in sess


def test_forged_session_key_has_no_effect_for_non_admin(user_client):
    with user_client.session_transaction() as sess:
        sess["preview_role"] = "user"
    html = user_client.get("/about").get_data(as_text=True)
    assert 'id="role-preview-banner"' not in html


def test_logout_and_login_clear_preview(admin_client):
    admin_client.post("/admin/preview", data={"role": "user"})
    admin_client.post("/logout")
    with admin_client.session_transaction() as sess:
        assert "preview_role" not in sess
    admin_client.post("/login/local", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert admin_client.get("/admin/users").status_code == 200
