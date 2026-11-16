import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from authlib.integrations.flask_client import OAuth
from flask import current_app, flash, redirect, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user
from werkzeug.security import check_password_hash, generate_password_hash

login_manager = LoginManager()
login_manager.login_view = "auth.login"
oauth = OAuth()


# Account roles, least to most privileged.
ROLES = ("user", "admin")
ROLE_LABELS = {"user": "Shifter (user)", "admin": "Administrator"}
PREVIEW_SESSION_KEY = "preview_role"


# Self-service profile fields beyond phone/institution (all TEXT, default '').
PROFILE_FIELDS = (
    "contact_email",
    "mobile_phone",
    "alt_contact_name",
    "alt_contact_phone",
    "chat_handle",
    "timezone",
)


class User(UserMixin):
    def __init__(self, row: sqlite3.Row):
        self.id = str(row["id"])
        self.email = row["email"]
        self.name = row["name"] or row["email"]
        self.role = row["role"] or "user"
        # Role preview: an admin may view the site as a lower role. real_role
        # keeps the stored role; role/is_admin reflect the previewed one.
        self.real_role = self.role
        self.previewing = False
        self.auth_provider = row["auth_provider"] or "local"
        keys = row.keys()
        for field in ("phone", "institution", "sso_name") + PROFILE_FIELDS:
            setattr(self, field, (row[field] if field in keys else "") or "")
        self.name_custom = bool(row["name_custom"]) if "name_custom" in keys else False

    @property
    def contact_address(self) -> str:
        """Email shown to other shifters and used for reminders."""
        return self.contact_email or self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def apply_preview(self, role: Optional[str]) -> None:
        """Act as *role* for this request; only real admins may preview."""
        if self.real_role == "admin" and role in ROLES and role != "admin":
            self.role = role
            self.previewing = True


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    return Path(current_app.config["AUTH_DB_PATH"])


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db(app) -> None:
    path = Path(app.config["AUTH_DB_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                auth_provider TEXT NOT NULL DEFAULT 'oidc',
                oidc_sub TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_oidc_sub ON users(oidc_sub)")
        # Migration: contact fields for existing databases.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        for column in ("phone", "institution", "sso_name") + PROFILE_FIELDS:
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        # name_custom=1: the user chose their display name; SSO logins keep it.
        if "name_custom" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN name_custom INTEGER NOT NULL DEFAULT 0")


def seed_admin(app) -> None:
    password = os.environ.get("MU2E_INITIAL_ADMIN_PASSWORD", "")
    if not password:
        return

    email = os.environ.get("MU2E_INITIAL_ADMIN_EMAIL", "mu2e-admin@fnal.gov").strip().lower()
    username = os.environ.get("MU2E_INITIAL_ADMIN_USERNAME", "mu2e-admin").strip()
    now = _utcnow()

    with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        password_hash = generate_password_hash(password)
        if existing:
            conn.execute(
                """
                UPDATE users
                   SET name = ?, role = 'admin', auth_provider = 'local',
                       password_hash = ?, updated_at = ?
                 WHERE email = ?
                """,
                (username, password_hash, now, email),
            )
        else:
            conn.execute(
                """
                INSERT INTO users
                    (email, name, role, auth_provider, password_hash, created_at, updated_at)
                VALUES (?, ?, 'admin', 'local', ?, ?, ?)
                """,
                (email, username, password_hash, now, now),
            )


def configure_oauth(app) -> None:
    oauth.init_app(app)

    provider_url = app.config.get("OIDC_PROVIDER_URL", "").strip()
    client_id = app.config.get("OIDC_CLIENT_ID", "").strip()
    client_secret = app.config.get("OIDC_CLIENT_SECRET", "").strip()
    if not (provider_url and client_id and client_secret):
        return

    discovery_url = (
        provider_url
        if provider_url.endswith("/.well-known/openid-configuration")
        else provider_url.rstrip("/") + "/.well-known/openid-configuration"
    )
    oauth.register(
        name="fermilab",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=discovery_url,
        client_kwargs={"scope": "openid email profile"},
    )


def get_user(user_id: str) -> Optional[User]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row) if row else None


def list_users() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT id, email, name, role, auth_provider, phone, institution,
                   created_at, updated_at, last_login_at
              FROM users
             ORDER BY role = 'admin' DESC, email COLLATE NOCASE
            """
        ).fetchall()


def admin_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT count(*) FROM users WHERE role = 'admin'").fetchone()[0])


def update_user_role(user_id: str, role: str, acting_user_id: str) -> tuple[bool, str]:
    if role not in ROLES:
        return False, "Invalid role."

    with connect() as conn:
        row = conn.execute("SELECT id, email, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False, "User not found."

        if row["role"] == "admin" and role != "admin" and admin_count() <= 1:
            return False, "At least one administrator account is required."

        conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
            (role, _utcnow(), user_id),
        )
        conn.commit()

    if str(user_id) == str(acting_user_id) and role != "admin":
        return True, "Your role was updated. You are no longer an administrator."
    return True, f"Updated {row['email']} to {role}."


def update_user_contact(user_id: str, phone: str, institution: str) -> Optional[User]:
    """Set a user's contact fields; returns the updated User (or None)."""
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE users SET phone = ?, institution = ?, updated_at = ? WHERE id = ?",
            (phone.strip(), institution.strip(), _utcnow(), user_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row)


def update_user_profile(user_id: str, fields: dict) -> Optional[User]:
    """Set a user's own contact fields (phone, institution, PROFILE_FIELDS).

    Only keys present in *fields* are written; values are stripped. The display
    name is changed separately with set_display_name (it triggers a rename).
    """
    allowed = ("phone", "institution") + PROFILE_FIELDS
    updates = {k: str(v or "").strip() for k, v in fields.items() if k in allowed}
    with connect() as conn:
        if not conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone():
            return None
        if updates:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), _utcnow(), user_id),
            )
            conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row)


def find_user_by_name(name: str, exclude_id: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Another account whose display name equals *name* (case-insensitive)."""
    with connect() as conn:
        return conn.execute(
            "SELECT id, email, name FROM users WHERE name = ? COLLATE NOCASE AND id != ?",
            ((name or "").strip(), exclude_id or -1),
        ).fetchone()


def set_display_name(user_id: str, name: str, custom: bool) -> Optional[User]:
    """Store a display name. custom=False means it tracks the SSO name again."""
    with connect() as conn:
        conn.execute(
            "UPDATE users SET name = ?, name_custom = ?, updated_at = ? WHERE id = ?",
            (name.strip(), 1 if custom else 0, _utcnow(), user_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row) if row else None


@login_manager.user_loader
def load_user(user_id: str) -> Optional[User]:
    user = get_user(user_id)
    if user is not None:
        user.apply_preview(session.get(PREVIEW_SESSION_KEY))
    return user


def authenticate_local(email: str, password: str) -> Optional[User]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if not row or not row["password_hash"]:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (_utcnow(), _utcnow(), row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return User(row)


def upsert_oidc_user(claims: dict) -> User:
    sub = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip().lower()
    if not sub or not email:
        raise ValueError("OIDC response did not include both sub and email claims.")

    name = (
        claims.get("name")
        or " ".join(p for p in [claims.get("given_name", ""), claims.get("family_name", "")] if p).strip()
        or claims.get("preferred_username")
        or email
    )
    now = _utcnow()

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE oidc_sub = ? OR email = ? ORDER BY oidc_sub = ? DESC LIMIT 1",
            (sub, email, sub),
        ).fetchone()
        if row:
            # A user-chosen display name survives SSO logins; sso_name always
            # tracks the provider so the user can revert to it.
            keep_custom = "name_custom" in row.keys() and row["name_custom"]
            conn.execute(
                """
                UPDATE users
                   SET email = ?, name = ?, sso_name = ?, auth_provider = 'oidc',
                       oidc_sub = ?, updated_at = ?, last_login_at = ?
                 WHERE id = ?
                """,
                (email, row["name"] if keep_custom else name, name, sub, now, now, row["id"]),
            )
            user_id = row["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO users
                    (email, name, sso_name, role, auth_provider, oidc_sub, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, 'user', 'oidc', ?, ?, ?, ?)
                """,
                (email, name, name, sub, now, now, now),
            )
            user_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(row)


def init_login(app) -> None:
    from flask_login import user_logged_in, user_logged_out

    login_manager.init_app(app)
    configure_oauth(app)
    app.jinja_env.globals["preview_roles"] = [
        (role, ROLE_LABELS[role]) for role in ROLES if role != "admin"
    ]

    # A role preview never outlives the login session that started it.
    def _clear_preview(sender, **extra):
        session.pop(PREVIEW_SESSION_KEY, None)

    user_logged_in.connect(_clear_preview, app, weak=False)
    user_logged_out.connect(_clear_preview, app, weak=False)

    @app.before_request
    def require_login():
        public_endpoints = {
            "auth.login",
            "auth.local_login",
            "auth.oidc_login",
            "auth.oidc_callback",
            "static",
        }
        if request.endpoint in public_endpoints:
            return None
        if request.endpoint is None:
            return None
        # /api/v1 authenticates with bearer tokens, not the login session.
        if request.endpoint.startswith("api."):
            return None
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return None


def oidc_enabled() -> bool:
    return bool(current_app.config.get("OIDC_PROVIDER_URL") and current_app.config.get("OIDC_CLIENT_ID") and current_app.config.get("OIDC_CLIENT_SECRET"))


def oidc_authorize_redirect():
    nonce = secrets.token_urlsafe(24)
    session["oidc_nonce"] = nonce
    callback_url = url_for("auth.oidc_callback", _external=True)
    session["oidc_next"] = request.args.get("next") or url_for("main.calendar_view")
    return oauth.fermilab.authorize_redirect(callback_url, nonce=nonce)


def oidc_authorize_callback() -> User:
    token = oauth.fermilab.authorize_access_token()
    nonce = session.pop("oidc_nonce", None)
    claims = {}
    if token.get("id_token"):
        claims = dict(oauth.fermilab.parse_id_token(token, nonce=nonce))
    if not claims:
        claims = dict(oauth.fermilab.get("userinfo").json())
    return upsert_oidc_user(claims)


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        if not getattr(current_user, "is_admin", False):
            flash("Administrator access is required.", "danger")
            return redirect(url_for("main.welcome"))
        return view(*args, **kwargs)

    return wrapped
