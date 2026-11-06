"""
Token-authenticated JSON API (``/api/v1``).

Every endpoint except ``/health`` requires ``Authorization: Bearer <token>``
where the token was minted on the Admin > API Tokens page. The login-session
guard in ``app.auth`` skips this blueprint, so a missing or bad token yields a
401 JSON body rather than a redirect to the login page.

Times are naive local time in ``API_TIMEZONE`` (default America/Chicago),
matching how shifts are stored. ``?at=`` accepts an ISO-8601 datetime, with or
without an offset; an aware value is converted to the API timezone first.
"""
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, g, jsonify, request

from app import store

bp = Blueprint("api", __name__, url_prefix="/api/v1")

DEFAULT_TIMEZONE = "America/Chicago"


def _timezone() -> ZoneInfo:
    return ZoneInfo(current_app.config.get("API_TIMEZONE") or DEFAULT_TIMEZONE)


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("X-API-Token", "").strip()


def token_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = _bearer_token()
        if not token:
            return jsonify({"error": "missing bearer token"}), 401
        row = store.verify_api_token(token)
        if row is None:
            return jsonify({"error": "invalid or revoked token"}), 401
        g.api_token = row
        return view(*args, **kwargs)

    return wrapped


def _parse_at(raw: str, tz: ZoneInfo) -> datetime:
    """ISO-8601 -> naive local datetime in ``tz``. Blank means now."""
    if not raw:
        return datetime.now(tz).replace(tzinfo=None)
    value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if value.tzinfo is not None:
        value = value.astimezone(tz).replace(tzinfo=None)
    return value


@bp.route("/health")
def health():
    return jsonify({"ok": True, "service": "mu2e-shift-scheduler"})


@bp.route("/classifications")
@token_required
def classifications():
    rows = [
        {"id": c["id"], "name": c["name"], "slug": c["slug"], "sort_order": c["sort_order"]}
        for c in store.list_classifications()
    ]
    return jsonify({"classifications": rows})


@bp.route("/schedules")
@token_required
def schedules():
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "slug": s["slug"],
            "classification": s.get("classification_name") or "",
            "classification_slug": s.get("classification_slug") or "",
        }
        for s in store.list_schedules()
    ]
    return jsonify({"schedules": rows})


@bp.route("/on-duty")
@token_required
def on_duty():
    tz = _timezone()
    try:
        at = _parse_at(request.args.get("at", ""), tz)
    except ValueError:
        return jsonify({"error": "at must be an ISO-8601 datetime"}), 400
    wanted = {s.strip() for s in request.args.get("classification", "").split(",") if s.strip()}
    rows = store.on_duty(at)
    if wanted:
        rows = [r for r in rows if r["classification_slug"] in wanted]
    return jsonify({
        "at": at.replace(tzinfo=tz).isoformat(timespec="minutes"),
        "timezone": str(tz),
        "on_duty": rows,
    })
