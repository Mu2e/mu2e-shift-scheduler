from flask import Flask


def create_app(config_path: str = "config.yaml") -> Flask:
    app = Flask(__name__)
    app.secret_key = "mu2e-shift-scheduler-change-in-production"
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit
    app.config["SCHEDULER_CONFIG"] = config_path

    from .routes import bp
    app.register_blueprint(bp)

    return app
