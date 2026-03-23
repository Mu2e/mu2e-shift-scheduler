from typing import Optional

from flask import Flask


def create_app(
    config_path: str = "config.yaml",
    preferences_shifts_csv: Optional[str] = None,
    preferences_json: str = "preferences.json",
) -> Flask:
    app = Flask(__name__)
    app.secret_key = "mu2e-shift-scheduler-change-in-production"
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit
    app.config["SCHEDULER_CONFIG"] = config_path
    app.config["PREFERENCES_SHIFTS_CSV"] = preferences_shifts_csv
    app.config["PREFERENCES_JSON"] = preferences_json

    from .routes import bp
    app.register_blueprint(bp)

    from .preferences import bp as pref_bp
    app.register_blueprint(pref_bp)

    return app
