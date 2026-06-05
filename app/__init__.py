import os
from pathlib import Path
from typing import Optional

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix


def _read_secret_env(name: str, file_name: str) -> str:
    path = os.environ.get(file_name, "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return os.environ.get(name, "").strip()


def create_app(
    config_path: str = "config/config.yaml",
    preferences_shifts_csv: Optional[str] = None,
    preferences_json: str = "preferences.json",
) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
    app.secret_key = os.environ.get(
        "FLASK_SECRET_KEY",
        "mu2e-shift-scheduler-change-in-production",
    )
    app.config["PREFERRED_URL_SCHEME"] = os.environ.get("PREFERRED_URL_SCHEME", "https")
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024)
    )
    app.config["SCHEDULER_CONFIG"] = os.environ.get(
        "SCHEDULER_CONFIG",
        config_path,
    )
    app.config["PREFERENCES_SHIFTS_CSV"] = os.environ.get(
        "PREFERENCES_SHIFTS_CSV",
        preferences_shifts_csv or "",
    )
    app.config["PREFERENCES_JSON"] = os.environ.get(
        "PREFERENCES_JSON",
        preferences_json,
    )
    app.config["CSV_DIR"] = os.environ.get(
        "CSV_DIR",
        str(Path(app.config["PREFERENCES_SHIFTS_CSV"]).parent)
        if app.config["PREFERENCES_SHIFTS_CSV"]
        else "csv",
    )
    app.config["DATA_DIR"] = os.environ.get("DATA_DIR", "data")
    app.config["AUTH_DB_PATH"] = os.environ.get("AUTH_DB_PATH", "data/users.sqlite")
    app.config["OIDC_PROVIDER_URL"] = os.environ.get("OIDC_PROVIDER_URL", "").strip()
    app.config["OIDC_CLIENT_ID"] = os.environ.get("OIDC_CLIENT_ID", "").strip()
    app.config["OIDC_CLIENT_SECRET"] = _read_secret_env(
        "OIDC_CLIENT_SECRET",
        "OIDC_CLIENT_SECRET_FILE",
    )
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    from .routes import bp
    app.register_blueprint(bp)

    from .preferences import bp as pref_bp
    app.register_blueprint(pref_bp)

    from .auth import init_auth_db, init_login, seed_admin
    from .auth_routes import bp as auth_bp
    app.register_blueprint(auth_bp)
    init_auth_db(app)
    seed_admin(app)
    init_login(app)

    return app
