"""Tests for the container file storage routes and page smoke rendering."""
import io
from pathlib import Path

SHIFTS_CSV = (
    "shift_id,date,start_time,end_time,points\n"
    "s1,2026-04-01,08:00,16:00,1.0\n"
    "s2,2026-04-02,08:00,16:00,1.0\n"
)


def test_pages_render_for_admin(admin_client):
    for url in ("/", "/calendar", "/schedules", "/schedule", "/configuration",
                "/admin/shift-setup", "/about", "/admin/users"):
        response = admin_client.get(url, follow_redirects=True)
        assert response.status_code == 200, f"{url} -> {response.status_code}"


def test_login_redirects_to_calendar(admin_client):
    response = admin_client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert "/calendar" in response.headers["Location"]


def test_api_files_lists_csv(admin_client, app):
    (Path(app.config["CSV_DIR"]) / "sample.csv").write_text(SHIFTS_CSV, encoding="utf-8")
    response = admin_client.get("/api/files?dir=csv")
    assert response.status_code == 200
    names = [f["name"] for f in response.get_json()["files"]]
    assert "sample.csv" in names

    assert admin_client.get("/api/files?dir=bogus").status_code == 400


def test_download_stored_file(admin_client, app):
    (Path(app.config["CSV_DIR"]) / "dl.csv").write_text(SHIFTS_CSV, encoding="utf-8")
    response = admin_client.get("/files/download?dir=csv&name=dl.csv")
    assert response.status_code == 200
    assert b"shift_id" in response.data


def test_download_blocks_protected_and_traversal(admin_client, app):
    data_dir = Path(app.config["DATA_DIR"])
    assert (data_dir / "users.sqlite").exists()
    # Protected names and traversal attempts must not be served.
    for name in ("users.sqlite", "app.sqlite", "preferences.json", "../users.sqlite"):
        response = admin_client.get(
            f"/files/download?dir=data&name={name}", follow_redirects=False
        )
        assert response.status_code == 302, name  # redirected with a flash, not served


def test_upload_stored_file_requires_admin(user_client):
    response = user_client.post(
        "/files/upload",
        data={"dir": "csv", "file": (io.BytesIO(SHIFTS_CSV.encode()), "up.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    # Non-admin is bounced by admin_required
    assert response.status_code == 302


def test_upload_stored_file_validates_csv(admin_client, app):
    response = admin_client.post(
        "/files/upload",
        data={"dir": "csv", "file": (io.BytesIO(b"not,a,schedule\n1,2,3\n"), "bad.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert not (Path(app.config["CSV_DIR"]) / "bad.csv").exists()

    response = admin_client.post(
        "/files/upload",
        data={"dir": "csv", "file": (io.BytesIO(SHIFTS_CSV.encode()), "good.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert (Path(app.config["CSV_DIR"]) / "good.csv").exists()


def test_anonymous_is_redirected_to_login(client):
    response = client.get("/calendar", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
