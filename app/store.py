"""
SQLite persistence for named schedules, classifications (taxonomy), saved
assignments, app settings, and shifter contact info.

All SQL for these tables lives in this module (stdlib sqlite3, same pattern as
app/auth.py). The statements use ``?`` placeholders and ``ON CONFLICT`` upserts
that are also valid Postgres syntax, so a future Postgres migration only needs
to replace ``connect()`` and the pragma block.

Concurrency: gunicorn runs multiple workers against this one file, so
connections enable WAL and a busy timeout. That is only safe with a single
replica (the Helm chart deploys one pod with the Recreate strategy); running
multiple replicas requires switching to Postgres.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from scheduler.blocks import slugify
from scheduler.loader import Shift, _default_points

DEFAULT_CLASSIFICATIONS = ["General Shifts", "Run Coordinators", "Oncall DAQ Experts"]

SETTING_DEFAULT_SCHEDULE = "default_schedule_id"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    slug       TEXT NOT NULL UNIQUE,
    show_tab   INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL COLLATE NOCASE UNIQUE,
    slug              TEXT NOT NULL UNIQUE,
    classification_id INTEGER REFERENCES classifications(id) ON DELETE SET NULL,
    source            TEXT NOT NULL DEFAULT 'upload',
    csv_filename      TEXT,
    created_by        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_shifts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id  INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    shift_id     TEXT NOT NULL,
    shift_name   TEXT NOT NULL DEFAULT '',
    block_name   TEXT NOT NULL DEFAULT '',
    block_type   TEXT NOT NULL DEFAULT '',
    week         INTEGER,
    block_number INTEGER,
    days         TEXT NOT NULL DEFAULT '',
    date         TEXT NOT NULL,
    date_end     TEXT NOT NULL DEFAULT '',
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    points       REAL,
    UNIQUE (schedule_id, shift_id)
);
CREATE INDEX IF NOT EXISTS idx_shifts_sched_date ON schedule_shifts(schedule_id, date);

CREATE TABLE IF NOT EXISTS assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id  INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    shift_id     TEXT NOT NULL,
    person       TEXT NOT NULL DEFAULT '',
    institution  TEXT NOT NULL DEFAULT '',
    is_preferred INTEGER,
    pref_rank    INTEGER,
    points       REAL,
    saved_by     TEXT NOT NULL DEFAULT '',
    saved_at     TEXT NOT NULL,
    UNIQUE (schedule_id, shift_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email       TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    institution TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    return Path(current_app.config["APP_DB_PATH"])


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_app_db(app) -> None:
    """Create tables and seed the default taxonomy (idempotent)."""
    path = Path(app.config["APP_DB_PATH"])
    with connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '1')"
        )
        now = _utcnow()
        for order, name in enumerate(DEFAULT_CLASSIFICATIONS):
            conn.execute(
                """
                INSERT OR IGNORE INTO classifications (name, slug, show_tab, sort_order, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (name, slugify(name), order, now, now),
            )
    # Context-manager commit only wraps transactions; close explicitly.


# ---------------------------------------------------------------------------
# Classifications (taxonomy)
# ---------------------------------------------------------------------------

def list_classifications(visible_only: bool = False) -> list[dict]:
    query = """
        SELECT c.*, (SELECT count(*) FROM schedules s WHERE s.classification_id = c.id) AS n_schedules
          FROM classifications c
    """
    if visible_only:
        query += " WHERE c.show_tab = 1"
    query += " ORDER BY c.sort_order, c.name COLLATE NOCASE"
    with connect() as conn:
        return [dict(row) for row in conn.execute(query).fetchall()]


def get_classification(cid: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM classifications WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def get_classification_by_slug(slug: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM classifications WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def create_classification(name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Enter a classification name.")
    now = _utcnow()
    with connect() as conn:
        slug = _unique_slug(conn, "classifications", slugify(name))
        max_order = conn.execute("SELECT coalesce(max(sort_order), -1) FROM classifications").fetchone()[0]
        try:
            cur = conn.execute(
                """
                INSERT INTO classifications (name, slug, show_tab, sort_order, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (name, slug, max_order + 1, now, now),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"A classification named '{name}' already exists.")
        return cur.lastrowid


def update_classification(cid: int, *, name: str | None = None,
                          show_tab: bool | None = None,
                          sort_order: int | None = None) -> None:
    sets = []
    params: list = []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Enter a classification name.")
        sets += ["name = ?"]
        params += [name]
    if show_tab is not None:
        sets += ["show_tab = ?"]
        params += [1 if show_tab else 0]
    if sort_order is not None:
        sets += ["sort_order = ?"]
        params += [sort_order]
    if not sets:
        return
    sets += ["updated_at = ?"]
    params += [_utcnow(), cid]
    with connect() as conn:
        try:
            conn.execute(f"UPDATE classifications SET {', '.join(sets)} WHERE id = ?", params)
        except sqlite3.IntegrityError:
            raise ValueError(f"A classification named '{name}' already exists.")


def delete_classification(cid: int) -> None:
    with connect() as conn:
        n = conn.execute(
            "SELECT count(*) FROM schedules WHERE classification_id = ?", (cid,)
        ).fetchone()[0]
        if n:
            raise ValueError(
                f"Cannot delete: {n} schedule(s) still use this classification."
            )
        conn.execute("DELETE FROM classifications WHERE id = ?", (cid,))


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

_SHIFT_COLUMNS = (
    "shift_id", "shift_name", "block_name", "block_type", "week",
    "block_number", "days", "date", "date_end", "start_time", "end_time", "points",
)


def _unique_slug(conn: sqlite3.Connection, table: str, base: str, exclude_id: int | None = None) -> str:
    slug = base
    counter = 2
    while True:
        if exclude_id is None:
            row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
        else:
            row = conn.execute(
                f"SELECT id FROM {table} WHERE slug = ? AND id != ?", (slug, exclude_id)
            ).fetchone()
        if row is None:
            return slug
        slug = f"{base}-{counter}"
        counter += 1


def _normalize_shift_row(row: dict) -> dict:
    def _int(value):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _float(value):
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    return {
        "shift_id": str(row.get("shift_id", "")).strip(),
        "shift_name": str(row.get("shift_name") or row.get("shift_type") or "").strip(),
        "block_name": str(row.get("block_name", "")).strip(),
        "block_type": str(row.get("block_type", "")).strip(),
        "week": _int(row.get("week")),
        "block_number": _int(row.get("block_number")),
        "days": str(row.get("days", "")).strip(),
        "date": str(row.get("date", "")).strip(),
        "date_end": str(row.get("date_end", "")).strip(),
        "start_time": str(row.get("start_time", "")).strip(),
        "end_time": str(row.get("end_time", "")).strip(),
        "points": _float(row.get("points")),
    }


def upsert_schedule(name: str, classification_id: int | None, shifts: list[dict],
                    *, source: str, created_by: str = "",
                    csv_filename: str | None = None) -> tuple[int, int]:
    """Create or overwrite (by case-insensitive name) a named schedule.

    Returns (schedule_id, n_dropped_assignments) where the second value counts
    saved assignments whose shift_id no longer exists in the new shift list.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Enter a schedule name.")
    normalized = [_normalize_shift_row(r) for r in shifts]
    normalized = [r for r in normalized if r["shift_id"]]
    if not normalized:
        raise ValueError("The schedule contains no shifts.")
    ids = [r["shift_id"] for r in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("The schedule contains duplicate shift IDs.")

    now = _utcnow()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, slug FROM schedules WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            schedule_id = existing["id"]
            conn.execute(
                """
                UPDATE schedules
                   SET name = ?, classification_id = ?, source = ?, csv_filename = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (name, classification_id, source, csv_filename, now, schedule_id),
            )
        else:
            slug = _unique_slug(conn, "schedules", slugify(name))
            cur = conn.execute(
                """
                INSERT INTO schedules
                    (name, slug, classification_id, source, csv_filename, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, slug, classification_id, source, csv_filename, created_by, now, now),
            )
            schedule_id = cur.lastrowid

        conn.execute("DELETE FROM schedule_shifts WHERE schedule_id = ?", (schedule_id,))
        conn.executemany(
            f"""
            INSERT INTO schedule_shifts (schedule_id, {', '.join(_SHIFT_COLUMNS)})
            VALUES (?, {', '.join('?' for _ in _SHIFT_COLUMNS)})
            """,
            [tuple([schedule_id] + [r[c] for c in _SHIFT_COLUMNS]) for r in normalized],
        )

        placeholders = ",".join("?" for _ in ids)
        dropped = conn.execute(
            f"SELECT count(*) FROM assignments WHERE schedule_id = ? AND shift_id NOT IN ({placeholders})",
            [schedule_id] + ids,
        ).fetchone()[0]
        if dropped:
            conn.execute(
                f"DELETE FROM assignments WHERE schedule_id = ? AND shift_id NOT IN ({placeholders})",
                [schedule_id] + ids,
            )
        return schedule_id, dropped


def list_schedules(classification_id: int | None = None) -> list[dict]:
    query = """
        SELECT s.*, c.name AS classification_name, c.slug AS classification_slug,
               (SELECT count(*) FROM schedule_shifts ss WHERE ss.schedule_id = s.id) AS n_shifts,
               (SELECT count(*) FROM assignments a
                 WHERE a.schedule_id = s.id
                   AND a.person != '' AND a.person != 'UNASSIGNED') AS n_assigned
          FROM schedules s
          LEFT JOIN classifications c ON c.id = s.classification_id
    """
    params: tuple = ()
    if classification_id is not None:
        query += " WHERE s.classification_id = ?"
        params = (classification_id,)
    query += " ORDER BY s.updated_at DESC, s.name COLLATE NOCASE"
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_schedule(schedule_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, c.name AS classification_name, c.slug AS classification_slug
              FROM schedules s
              LEFT JOIN classifications c ON c.id = s.classification_id
             WHERE s.id = ?
            """,
            (schedule_id,),
        ).fetchone()
    return dict(row) if row else None


def get_schedule_by_name(name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, c.name AS classification_name, c.slug AS classification_slug
              FROM schedules s
              LEFT JOIN classifications c ON c.id = s.classification_id
             WHERE s.name = ?
            """,
            ((name or "").strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_schedule_shifts(schedule_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM schedule_shifts
             WHERE schedule_id = ?
             ORDER BY date, start_time, shift_id
            """,
            (schedule_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_schedule(schedule_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def schedule_to_shift_objects(schedule_id: int, config: dict) -> list:
    """Build solver-ready Shift objects; NULL points get the loader defaults."""
    sp_config = (config or {}).get("shift_points", {})
    shifts = []
    for row in get_schedule_shifts(schedule_id):
        points = row["points"]
        if points is None:
            points = _default_points(row["start_time"], sp_config)
        shifts.append(
            Shift(
                shift_id=row["shift_id"],
                date=row["date"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                points=float(points),
            )
        )
    return shifts


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def save_assignments(schedule_id: int, results: list[dict], saved_by: str) -> None:
    # Assignment rows may carry email/phone from the people CSV; record them so
    # calendar contact links keep working after the in-session results expire.
    bulk_upsert_contacts(results)
    now = _utcnow()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO assignments
                (schedule_id, shift_id, person, institution, is_preferred, pref_rank, points, saved_by, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schedule_id, shift_id) DO UPDATE SET
                person = excluded.person,
                institution = excluded.institution,
                is_preferred = excluded.is_preferred,
                pref_rank = excluded.pref_rank,
                points = excluded.points,
                saved_by = excluded.saved_by,
                saved_at = excluded.saved_at
            """,
            [
                (
                    schedule_id,
                    str(r.get("shift_id", "")).strip(),
                    str(r.get("person", "")).strip(),
                    str(r.get("institution", "")).strip(),
                    1 if r.get("is_preferred") else 0,
                    r.get("pref_rank"),
                    r.get("points"),
                    saved_by,
                    now,
                )
                for r in results
                if str(r.get("shift_id", "")).strip()
            ],
        )


def get_assignments(schedule_id: int) -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM assignments WHERE schedule_id = ?", (schedule_id,)
        ).fetchall()
    return {row["shift_id"]: dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _utcnow()),
        )


def get_default_schedule_id() -> int | None:
    value = get_setting(SETTING_DEFAULT_SCHEDULE)
    try:
        return int(value) if value else None
    except ValueError:
        return None


def set_default_schedule_id(schedule_id: int | None) -> None:
    set_setting(SETTING_DEFAULT_SCHEDULE, str(schedule_id) if schedule_id else "")


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def upsert_contact(name: str, *, email: str = "", phone: str = "", institution: str = "") -> None:
    name = (name or "").strip()
    if not name:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO contacts (name, email, phone, institution, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                email = CASE WHEN excluded.email != '' THEN excluded.email ELSE contacts.email END,
                phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE contacts.phone END,
                institution = CASE WHEN excluded.institution != '' THEN excluded.institution ELSE contacts.institution END,
                updated_at = excluded.updated_at
            """,
            (name, email.strip(), phone.strip(), institution.strip(), _utcnow()),
        )


def bulk_upsert_contacts(rows: list[dict]) -> None:
    for row in rows:
        email = str(row.get("email", "") or "").strip()
        phone = str(row.get("phone", "") or "").strip()
        if not email and not phone:
            continue
        upsert_contact(
            str(row.get("person") or row.get("name") or "").strip(),
            email=email,
            phone=phone,
            institution=str(row.get("institution", "") or "").strip(),
        )


def get_contact(name: str) -> dict | None:
    """Contact for a person: contacts table first, then the auth users table."""
    name = (name or "").strip()
    if not name or name.upper() == "UNASSIGNED":
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE name = ?", (name,)).fetchone()
    if row and (row["email"] or row["phone"]):
        return {
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "institution": row["institution"],
            "source": "contacts",
        }

    # Fallback: a user who has logged in (name match, or email local part).
    from app import auth

    with auth.connect() as conn:
        user = conn.execute(
            "SELECT name, email FROM users WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if user is None:
            local_part = name.lower().replace(" ", ".")
            user = conn.execute(
                "SELECT name, email FROM users WHERE lower(email) = ? OR lower(email) LIKE ?",
                (name.lower(), f"{local_part}@%"),
            ).fetchone()
    if user:
        return {
            "name": name,
            "email": user["email"],
            "phone": "",
            "institution": row["institution"] if row else "",
            "source": "users",
        }
    if row and row["institution"]:
        return {
            "name": row["name"],
            "email": "",
            "phone": "",
            "institution": row["institution"],
            "source": "contacts",
        }
    return None
