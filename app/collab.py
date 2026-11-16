"""
Mu2e Collaboration directory sync (https://mu2e-collaboration.fnal.gov).

The collaboration site is the source of record for the member directory. This
module reads it with a bearer token (GET only) and keeps a local copy in
app.sqlite (``collab_members``, SQL in app/store.py) so assignment suggestions,
contact popovers and reminders can use it without per-request API calls.

Contract (docs/API.md in the collaboration repo):
  * ``GET /api/v1/users/`` paginated envelope; each record has
    ``schema_version`` (we understand 1 and refuse anything newer).
  * ``?updated_since=`` for incremental sync. All records are fetched (not
    ``members_only``) so membership/active changes are seen; filtering is local.
  * Phones are not in the directory record. With ``fetch_phones`` and an
    admin-owned token, ``GET /users/<id>/?detail=full`` supplies
    ``profile.office_phone``/``mobile_phone`` for changed records only. That
    record is outside the versioned contract, so missing keys are tolerated.

Configuration precedence: command line > environment (incl. .env loaded by
the start script) > config.yaml ``collaboration:`` > defaults.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_BASE_URL = "https://mu2e-collaboration.fnal.gov"
USER_AGENT = "mu2e-shift-scheduler-collab-sync/1"

DEFAULTS = {
    "base_url": DEFAULT_BASE_URL,
    "token": "",
    "token_file": "",
    "sync_hours": 6.0,       # periodic in-app sync interval; 0 disables
    "fetch_phones": True,    # per-record ?detail=full lookups (admin-owned token)
    "members_only": True,    # only collaboration members are assignable
    "timeout": 30.0,
}

_ENV = {
    "base_url": "MU2E_COLLAB_URL",
    "token": "MU2E_COLLAB_TOKEN",
    "token_file": "MU2E_COLLAB_TOKEN_FILE",
    "sync_hours": "MU2E_COLLAB_SYNC_HOURS",
    "fetch_phones": "MU2E_COLLAB_FETCH_PHONES",
    "members_only": "MU2E_COLLAB_MEMBERS_ONLY",
    "timeout": "MU2E_COLLAB_TIMEOUT",
}


class CollabError(RuntimeError):
    """API failure. status is the HTTP status (None when unreachable)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SchemaVersionError(CollabError):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _coerce(key: str, value):
    if key in ("fetch_phones", "members_only"):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if key in ("sync_hours", "timeout"):
        return float(value)
    return str(value).strip()


def resolve_config(yaml_section: dict | None = None, overrides: dict | None = None,
                   environ: dict | None = None) -> dict:
    """Merge defaults < config.yaml < environment < command-line overrides.

    The token is read from token_file when no literal token is given. The
    returned dict has ``enabled`` (a token is available) and never logs it.
    """
    environ = os.environ if environ is None else environ
    cfg = dict(DEFAULTS)
    for key, value in (yaml_section or {}).items():
        if key in DEFAULTS and value is not None:
            cfg[key] = _coerce(key, value)
    for key, var in _ENV.items():
        if environ.get(var, "") != "":
            cfg[key] = _coerce(key, environ[var])
    for key, value in (overrides or {}).items():
        if key in DEFAULTS and value is not None:
            cfg[key] = _coerce(key, value)
    if not cfg["token"] and cfg["token_file"]:
        try:
            cfg["token"] = Path(cfg["token_file"]).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning("collaboration token file %s unreadable: %s", cfg["token_file"], exc.strerror)
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    cfg["enabled"] = bool(cfg["token"])
    return cfg


def load_yaml_section(config_path: str) -> dict:
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("collaboration") or {}
    except (OSError, ValueError):
        return {}


def public_config(cfg: dict) -> dict:
    """Config safe to display: the token is reduced to presence."""
    out = {k: v for k, v in cfg.items() if k != "token"}
    out["token_set"] = bool(cfg.get("token"))
    return out


# ---------------------------------------------------------------------------
# HTTP client (stdlib only; mu2e-collab is not published on PyPI)
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0, retries: int = 3):
        if not token:
            raise CollabError("No collaboration API token configured.")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.retries = retries

    def __repr__(self) -> str:  # never expose the token
        return f"Client({self.base_url!r})"

    def get(self, path: str, params: dict | None = None) -> dict:
        if path.startswith("/"):
            url = self.base_url + path
        else:
            url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        if params:
            sep = "&" if "?" in url else "?"
            url += sep + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                message = _error_message(exc)
                if exc.code >= 500 and attempt < self.retries:
                    log.warning("collaboration API %s on %s; retrying", exc.code, path)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise CollabError(f"HTTP {exc.code}: {message}", exc.code) from None
            except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
                if attempt < self.retries:
                    log.warning("collaboration API unreachable (%s); retrying", exc)
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise CollabError(f"Collaboration site unreachable: {exc}") from None
            except ValueError as exc:
                raise CollabError(f"Invalid JSON from collaboration API: {exc}") from None
        raise CollabError("unreachable")  # pragma: no cover

    def list_all(self, path: str, params: dict | None = None, page_size: int = 500):
        """Yield every record of a paginated collection, following ``next``."""
        page = self.get(path, {**(params or {}), "page_size": page_size})
        while True:
            _check_schema(page.get("schema_version"))
            for record in page.get("results", []):
                yield record
            nxt = page.get("next")
            if not nxt:
                return
            page = self.get(nxt if nxt.startswith("/") else "/" + nxt)


def _error_message(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
        return str(body.get("error") or exc.reason)
    except Exception:
        return str(exc.reason)


def _check_schema(version) -> None:
    if version is not None and int(version) > SUPPORTED_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Collaboration directory schema_version {version} is newer than the supported "
            f"version {SUPPORTED_SCHEMA_VERSION}; update the shift scheduler before syncing.")


# ---------------------------------------------------------------------------
# Record normalization
# ---------------------------------------------------------------------------

def normalize_user(rec: dict, profile: dict | None = None) -> dict:
    """Directory record (+ optional full-profile phones) -> collab_members row."""
    from app.store import collab_display_to_name, person_match_key

    _check_schema(rec.get("schema_version"))
    inst = rec.get("institution") or {}
    display = (rec.get("display_name") or "").strip()
    name = collab_display_to_name(display)
    profile = profile or {}

    def text(value) -> str:
        return "" if value is None else str(value).strip()

    return {
        "collab_id": int(rec["id"]),
        "member_number": text(rec.get("member_number")),
        "display_name": display,
        "name": name,
        "match_key": person_match_key(display),
        "email": text(rec.get("email")).lower(),
        "contact_email": text(rec.get("contact_email")).lower(),
        "fnal_username": text(rec.get("fnal_username")).lower(),
        "orcid": text(rec.get("orcid")),
        "institution": text(inst.get("name")) if isinstance(inst, dict) else "",
        "collaboration_position": text(rec.get("collaboration_position")),
        "office_phone": text(profile.get("office_phone")),
        "mobile_phone": text(profile.get("mobile_phone")),
        "is_active": 1 if rec.get("is_active", True) else 0,
        "is_collaboration_member": 1 if rec.get("is_collaboration_member") else 0,
        "collab_updated_at": text(rec.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync(cfg: dict, *, full: bool = False, dry_run: bool = False, client: Client | None = None) -> dict:
    """Run one sync inside an app context. Returns a status dict.

    The start time is recorded before fetching and stored as the next
    ``updated_since`` only after the whole run succeeds, so records changed
    during the run are fetched again rather than missed.
    """
    from app import store

    started = _now_iso()
    t0 = time.monotonic()
    status = {"started_at": started, "full": full, "dry_run": dry_run, "ok": False}
    try:
        client = client or Client(cfg["base_url"], cfg["token"], timeout=cfg["timeout"])
        who = client.get("whoami/")
        perms = who.get("permissions") or {}
        token_info = who.get("token") or {}
        status["token_owner"] = (who.get("user") or {}).get("username", "")
        status["token_mode"] = token_info.get("mode", "")
        status["token_expires_at"] = token_info.get("expires_at")
        can_full = bool(perms.get("is_admin"))
        want_phones = bool(cfg.get("fetch_phones")) and can_full
        if cfg.get("fetch_phones") and not can_full:
            status["warning"] = "Token owner is not an admin; phone numbers are not available."

        since = None if full else store.get_setting(store.SETTING_COLLAB_SINCE)
        params = {"updated_since": since} if since else {}
        records, phone_lookups, phone_failures = [], 0, 0
        for rec in client.list_all("users/", params):
            profile = None
            if want_phones:
                try:
                    detail = client.get(f"users/{int(rec['id'])}/", {"detail": "full"})
                    profile = detail.get("profile") or {}
                    phone_lookups += 1
                except CollabError as exc:
                    if exc.status in (401, 403):
                        raise
                    phone_failures += 1
            records.append(normalize_user(rec, profile))

        status["fetched"] = len(records)
        status["phone_lookups"] = phone_lookups
        status["phone_failures"] = phone_failures
        status["since"] = since
        if not dry_run:
            store.upsert_collab_members(records)
            store.set_setting(store.SETTING_COLLAB_SINCE, started)
        status["ok"] = True
    except CollabError as exc:
        status["error"] = str(exc)
        status["http_status"] = exc.status
        log.error("collaboration sync failed: %s", exc)
    status["finished_at"] = _now_iso()
    status["duration_s"] = round(time.monotonic() - t0, 2)
    if not dry_run:
        previous = store.get_collab_status()
        if not status["ok"] and previous.get("ok"):
            status["last_success_at"] = previous.get("finished_at")
        elif status["ok"]:
            status["last_success_at"] = status["finished_at"]
        store.set_collab_status(status)
    return status


def sync_with_lease(app, *, full: bool = False, min_interval_seconds: int = 0) -> dict | None:
    """Sync under the single-writer lease; None when another worker holds it
    (or the last success is more recent than min_interval_seconds)."""
    from app import store

    owner = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
    with app.app_context():
        if not store.acquire_collab_lease(owner, ttl_seconds=1800,
                                          min_interval_seconds=min_interval_seconds):
            return None
        try:
            return sync(app.config["COLLAB"], full=full)
        finally:
            store.release_collab_lease(owner)


def start_background_sync(app) -> threading.Thread | None:
    """Periodic daemon-thread sync (every gunicorn worker starts one; the
    lease and min interval make only one of them actually sync)."""
    cfg = app.config["COLLAB"]
    if not cfg["enabled"] or cfg["sync_hours"] <= 0 or app.config.get("TESTING"):
        return None
    interval = int(cfg["sync_hours"] * 3600)

    def loop():
        time.sleep(30)  # let the app finish starting
        while True:
            try:
                sync_with_lease(app, min_interval_seconds=max(interval - 60, 0))
            except Exception:  # never let the thread die
                log.exception("collaboration background sync crashed")
            time.sleep(min(interval, 900))

    thread = threading.Thread(target=loop, name="collab-sync", daemon=True)
    thread.start()
    return thread
