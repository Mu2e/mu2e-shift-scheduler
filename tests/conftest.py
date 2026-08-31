"""Shared pytest fixtures: an isolated app instance plus login helpers."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ADMIN_EMAIL = "admin@example.gov"
ADMIN_PASSWORD = "test-admin-password"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    csv_dir = tmp_path / "csv"
    data_dir = tmp_path / "data"
    csv_dir.mkdir()
    data_dir.mkdir()

    monkeypatch.setenv("CSV_DIR", str(csv_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTH_DB_PATH", str(data_dir / "users.sqlite"))
    monkeypatch.setenv("APP_DB_PATH", str(data_dir / "app.sqlite"))
    monkeypatch.setenv("SCHEDULER_CONFIG", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("PREFERENCES_SHIFTS_CSV", str(csv_dir / "shifts.csv"))
    monkeypatch.setenv("PREFERENCES_JSON", str(data_dir / "preferences.json"))
    monkeypatch.setenv("MU2E_INITIAL_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("MU2E_INITIAL_ADMIN_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("SHOW_ADMIN_LOGIN", "1")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    monkeypatch.delenv("OIDC_PROVIDER_URL", raising=False)
    monkeypatch.delenv("OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)

    from app import create_app

    application = create_app()
    application.config["TESTING"] = True
    application.config["PREFERRED_URL_SCHEME"] = "http"
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_client(app):
    """Test client logged in as the seeded local admin."""
    test_client = app.test_client()
    response = test_client.post(
        "/login/local",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 302
    return test_client


@pytest.fixture()
def user_client(app):
    """Test client logged in as a regular (non-admin) user."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
        cur = conn.execute(
            """
            INSERT INTO users (email, name, role, auth_provider, created_at, updated_at)
            VALUES (?, ?, 'user', 'oidc', ?, ?)
            """,
            ("shifter@example.gov", "Regular Shifter", now, now),
        )
        user_id = cur.lastrowid
    test_client = app.test_client()
    with test_client.session_transaction() as flask_session:
        flask_session["_user_id"] = str(user_id)
        flask_session["_fresh"] = True
    return test_client


def write_csv(path: Path, header: list, rows: list) -> Path:
    lines = [",".join(header)]
    lines += [",".join(str(cell) for cell in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def shifts_csv(tmp_path):
    return write_csv(
        tmp_path / "shifts.csv",
        ["shift_id", "date", "start_time", "end_time", "points"],
        [
            ["s1", "2026-04-01", "08:00", "16:00", "1.0"],
            ["s2", "2026-04-01", "16:00", "00:00", "1.0"],
            ["s3", "2026-04-02", "00:00", "08:00", "2.0"],
            ["s4", "2026-04-02", "08:00", "16:00", ""],
        ],
    )


@pytest.fixture()
def people_csv(tmp_path):
    return write_csv(
        tmp_path / "people.csv",
        ["name", "institution", "pref_1", "pref_2"],
        [
            ["Alice", "Fermilab", "s1", "s2"],
            ["Bob", "Argonne", "s3", ""],
        ],
    )
