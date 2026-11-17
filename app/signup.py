"""
Shift self-signup: shifters claim open shifts (or whole blocks) and release
shifts they hold, from the calendar context menu or the /signup page.

Identity comes from the logged-in account (SSO name, or the display name
chosen on My Profile, which is the string stored in assignments.person) with
contact details filled from the user's profile first and the synced Mu2e
Collaboration directory second. All SQL lives in app/store.py.

Configuration (precedence: command line > environment > .env > config.yaml
``signup:`` section > defaults):

    enabled                 MU2E_SIGNUP_ENABLED                 master switch
    allow_block_signup      MU2E_SIGNUP_ALLOW_BLOCK             "sign up for whole block"
    allow_release_assigned  MU2E_SIGNUP_ALLOW_RELEASE_ASSIGNED  may drop solver/admin assignments
    signup_cutoff_hours     MU2E_SIGNUP_CUTOFF_HOURS            no sign-up this close to start
    release_cutoff_hours    MU2E_SIGNUP_RELEASE_CUTOFF_HOURS    no release this close to start
    require_collab_member   MU2E_SIGNUP_REQUIRE_COLLAB_MEMBER   must match an active directory member
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import store
from app.auth import admin_required

log = logging.getLogger(__name__)

bp = Blueprint("signup", __name__)

DEFAULTS = {
    "enabled": True,
    "allow_block_signup": True,
    "allow_release_assigned": False,
    "signup_cutoff_hours": 0.0,
    "release_cutoff_hours": 0.0,
    "require_collab_member": False,
}
_ENV = {
    "enabled": "MU2E_SIGNUP_ENABLED",
    "allow_block_signup": "MU2E_SIGNUP_ALLOW_BLOCK",
    "allow_release_assigned": "MU2E_SIGNUP_ALLOW_RELEASE_ASSIGNED",
    "signup_cutoff_hours": "MU2E_SIGNUP_CUTOFF_HOURS",
    "release_cutoff_hours": "MU2E_SIGNUP_RELEASE_CUTOFF_HOURS",
    "require_collab_member": "MU2E_SIGNUP_REQUIRE_COLLAB_MEMBER",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _coerce(key: str, value):
    if isinstance(DEFAULTS[key], bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return max(0.0, float(value))


def load_yaml_section(config_path: str) -> dict:
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("signup") or {}
    except (OSError, ValueError):
        return {}


def resolve_config(yaml_section: dict | None = None, overrides: dict | None = None,
                   environ: dict | None = None) -> dict:
    """defaults < config.yaml < environment (.env is loaded into it) < overrides."""
    environ = os.environ if environ is None else environ
    cfg = dict(DEFAULTS)
    for source in (yaml_section or {}, {k: environ[v] for k, v in _ENV.items() if environ.get(v, "") != ""},
                   overrides or {}):
        for key, value in source.items():
            if key in DEFAULTS and value is not None:
                cfg[key] = _coerce(key, value)
    return cfg


def config() -> dict:
    return current_app.config.get("SIGNUP") or dict(DEFAULTS)


def local_now() -> datetime:
    """Naive local time in API_TIMEZONE, the zone shift times are stored in."""
    tz = ZoneInfo(current_app.config.get("API_TIMEZONE") or "America/Chicago")
    return datetime.now(tz).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def identity_for(user) -> dict:
    """Resolve the signing-up person from the account and the directory.

    person   - the assignments.person string (the account display name, which
               /profile renames consistently across stored records)
    aliases  - lower-case names that count as "this user" when deciding
               whether a held shift may be released: display name, SSO name,
               and the matched directory name (matched by email / FNAL
               username first, so a solver assignment made from a people CSV
               under the directory spelling is recognized)
    """
    member = store.find_collab_member(
        user.name, emails=[user.email, getattr(user, "contact_email", "")]
    )
    aliases = {n.strip().lower() for n in (user.name, getattr(user, "sso_name", "")) if n and n.strip()}
    if member and member.get("name"):
        aliases.add(member["name"].strip().lower())
    institution = user.institution or (member or {}).get("institution", "") or ""
    email = user.contact_address or (member or {}).get("contact_email", "") or (member or {}).get("email", "")
    phone = user.phone or (member or {}).get("office_phone", "") or (member or {}).get("mobile_phone", "")
    return {
        "person": (user.name or "").strip(),
        "aliases": aliases,
        "institution": institution,
        "email": email,
        "phone": phone,
        "member": member,
        "is_active_member": bool(member and member.get("is_active")),
    }


def eligibility_problem(ident: dict, cfg: dict) -> str:
    """Why this user may not self-sign-up at all ('' when they may)."""
    if not cfg.get("enabled"):
        return "Shift self-signup is disabled on this instance."
    if not ident["person"]:
        return "Your account has no display name; set one on My Profile."
    if cfg.get("require_collab_member") and not ident["is_active_member"]:
        return ("Self-signup requires an active Mu2e Collaboration member record matching your "
                "account email; contact your institution representative.")
    return ""


def _schedule_problem(schedule: dict | None) -> str:
    if schedule is None:
        return "Choose a stored schedule."
    if not schedule.get("signup_open", 1):
        return f"Schedule '{schedule['name']}' is closed to self-signup."
    return ""


# ---------------------------------------------------------------------------
# Eligibility per shift (calendar annotations and the signup page)
# ---------------------------------------------------------------------------

def _first_start(row: dict):
    intervals = store.shift_intervals(row)
    return intervals[0][0] if intervals else None


def shift_states(schedule: dict, ident: dict, cfg: dict, now: datetime | None = None) -> dict[str, dict]:
    """shift_id -> what the current user may do with it.

    Keys: person, mine, can_claim, can_release, release_reason, starts_at,
    block_ids (shift_ids of the block, [] when unblocked), can_claim_block.
    """
    now = now or local_now()
    signup_cutoff = now + timedelta(hours=cfg["signup_cutoff_hours"])
    release_cutoff = now + timedelta(hours=cfg["release_cutoff_hours"])
    usable = not eligibility_problem(ident, cfg) and not _schedule_problem(schedule)
    rows = store.signup_shift_rows(schedule["id"])

    blocks: dict[tuple, list[dict]] = {}
    for row in rows:
        key = store.block_key(row)
        if key is not None:
            blocks.setdefault(key, []).append(row)

    states: dict[str, dict] = {}
    for row in rows:
        starts = _first_start(row)
        person = row["person"].strip()
        mine = bool(person) and person.lower() in ident["aliases"]
        future_claim = starts is not None and starts > signup_cutoff
        release_reason = ""
        if mine:
            if starts is None or starts <= release_cutoff:
                release_reason = "already started or inside the release cutoff"
            elif not cfg["allow_release_assigned"] and row["assignment_source"] != "signup":
                release_reason = "assigned by the scheduler; ask a run coordinator"
        states[row["shift_id"]] = {
            "row": row,
            "person": person,
            "mine": mine,
            "starts_at": starts,
            "can_claim": usable and not person and future_claim,
            "can_release": usable and mine and not release_reason,
            "release_reason": release_reason,
            "block_ids": [],
            "can_claim_block": False,
        }
    for members in blocks.values():
        if len(members) < 2:
            continue
        ids = [m["shift_id"] for m in members]
        claimable = cfg["allow_block_signup"] and all(states[i]["can_claim"] for i in ids)
        for i in ids:
            states[i]["block_ids"] = ids
            states[i]["can_claim_block"] = claimable
    return states


def annotate_calendar(entries: dict, schedule: dict | None, user) -> bool:
    """Attach entry['signup'] for the calendar context menu. Returns True when
    any self-signup action is offered on this schedule."""
    if schedule is None or not getattr(user, "is_authenticated", False):
        return False
    cfg = config()
    ident = identity_for(user)
    if eligibility_problem(ident, cfg) or _schedule_problem(schedule):
        return False
    states = shift_states(schedule, ident, cfg)
    for day_entries in entries.values():
        for entry in day_entries:
            st = states.get(entry["shift_id"])
            if st:
                entry["signup"] = {
                    "can_claim": st["can_claim"],
                    "can_claim_block": st["can_claim_block"],
                    "block_size": len(st["block_ids"]),
                    "can_release": st["can_release"],
                    "mine": st["mine"],
                    "release_reason": st["release_reason"],
                }
    return True


def build_groups(states: dict[str, dict], show: str, include_past: bool, today: date) -> list[dict]:
    """Group shifts by block for the signup page, filtered by *show*
    ('open' = groups with something to claim, 'mine', 'all')."""
    groups: dict = {}
    for shift_id, st in states.items():
        row = st["row"]
        key = store.block_key(row) or ("shift", shift_id)
        groups.setdefault(key, {"rows": [], "key": key})["rows"].append(st)
    out = []
    for g in groups.values():
        first = g["rows"][0]["row"]
        last_day = first.get("date_end") or first["date"]
        if not include_past and last_day < today.isoformat():
            continue
        if show == "open" and not any(st["can_claim"] for st in g["rows"]):
            continue
        if show == "mine" and not any(st["mine"] for st in g["rows"]):
            continue
        g["date"] = first["date"]
        g["date_end"] = first.get("date_end") or ""
        g["days"] = first.get("days") or ""
        g["block_name"] = first.get("block_name") or ""
        g["week"] = first.get("week")
        g["is_block"] = len(g["rows"]) > 1
        g["can_claim_block"] = g["is_block"] and g["rows"][0]["can_claim_block"]
        g["n_open"] = sum(1 for st in g["rows"] if not st["person"])
        g["first_shift_id"] = g["rows"][0]["row"]["shift_id"]
        out.append(g)
    out.sort(key=lambda g: (g["date"], g["block_name"], g["first_shift_id"]))
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _safe_next(default: str) -> str:
    target = (request.form.get("next") or request.args.get("next") or "").strip()
    parsed = urlparse(target)
    if target.startswith("/") and not target.startswith("//") and not parsed.netloc and not parsed.scheme:
        return target
    return default


def _schedule_from_form() -> dict | None:
    try:
        return store.get_schedule(int(request.form.get("schedule_id", "")))
    except ValueError:
        return None


def _pick_schedule() -> dict | None:
    name = request.args.get("schedule", "").strip()
    if name:
        schedule = store.get_schedule_by_name(name)
        if schedule is None:
            flash(f"No schedule named '{name}' was found.", "warning")
        else:
            return schedule
    tab = request.args.get("tab", "").strip()
    default_id = store.get_default_schedule_id()
    if default_id:
        schedule = store.get_schedule(default_id)
        if schedule and (not tab or schedule.get("classification_slug") == tab):
            return schedule
    classification = store.get_classification_by_slug(tab) if tab else None
    candidates = store.list_schedules(classification["id"] if classification else None)
    open_first = [s for s in candidates if s.get("signup_open", 1)] or candidates
    return open_first[0] if open_first else None


@bp.route("/signup")
def signup_page():
    cfg = config()
    ident = identity_for(current_user)
    schedule = _pick_schedule()
    show = request.args.get("show", "open")
    if show not in {"open", "mine", "all"}:
        show = "open"
    include_past = request.args.get("past") == "1"
    groups, states = [], {}
    if schedule is not None:
        states = shift_states(schedule, ident, cfg)
        groups = build_groups(states, show, include_past, local_now().date())
    my_upcoming = sorted(
        (st for st in states.values() if st["mine"] and st["starts_at"] and st["starts_at"] >= local_now()),
        key=lambda st: st["starts_at"],
    )
    return render_template(
        "signup.html",
        nav_active="signup",
        cfg=cfg,
        ident=ident,
        problem=eligibility_problem(ident, cfg) or (_schedule_problem(schedule) if schedule else ""),
        schedule=schedule,
        schedules=store.list_schedules(),
        tabs=store.list_classifications(visible_only=True),
        active_tab=request.args.get("tab", "") or (schedule or {}).get("classification_slug") or "",
        show=show,
        include_past=include_past,
        groups=groups,
        my_upcoming=my_upcoming,
        signup_log=store.recent_signup_log(50) if current_user.is_admin else [],
    )


@bp.route("/signup/claim", methods=["POST"])
def signup_claim():
    cfg = config()
    schedule = _schedule_from_form()
    back = _safe_next(url_for("signup.signup_page", schedule=schedule["name"] if schedule else None))
    ident = identity_for(current_user)
    problem = eligibility_problem(ident, cfg) or _schedule_problem(schedule)
    if problem:
        flash(problem, "danger")
        return redirect(back)
    shift_id = request.form.get("shift_id", "").strip()
    scope = request.form.get("scope", "shift")
    if scope == "block":
        if not cfg["allow_block_signup"]:
            flash("Block sign-up is disabled; sign up for individual shifts.", "danger")
            return redirect(back)
        shift_ids = store.block_shift_ids(schedule["id"], shift_id)
    else:
        shift_ids = [shift_id]
    try:
        claimed = store.claim_shifts(
            schedule["id"], shift_ids, ident["person"],
            institution=ident["institution"], aliases=ident["aliases"],
            saved_by=current_user.email,
            not_before=local_now() + timedelta(hours=cfg["signup_cutoff_hours"]),
        )
    except store.SignupConflict as exc:
        flash(f"Sign-up failed: {exc}", "danger")
        return redirect(back)
    # Make the calendar contact link resolvable even when the profile is
    # sparse: profile values first, directory values fill blanks.
    store.upsert_contact(ident["person"], email=ident["email"], phone=ident["phone"],
                         institution=ident["institution"])
    log.info("signup: %s claimed %s in schedule %s", current_user.email,
             ",".join(r["shift_id"] for r in claimed), schedule["id"])
    if len(claimed) == 1:
        r = claimed[0]
        flash(f"You are signed up for {r['shift_name'] or r['shift_id']} on {r['date']} "
              f"({r['start_time']}–{r['end_time']}).", "success")
    else:
        flash(f"You are signed up for {len(claimed)} shifts in the "
              f"{claimed[0].get('block_name') or 'selected'} block starting {claimed[0]['date']}.", "success")
    return redirect(back)


@bp.route("/signup/release", methods=["POST"])
def signup_release():
    cfg = config()
    schedule = _schedule_from_form()
    back = _safe_next(url_for("signup.signup_page", schedule=schedule["name"] if schedule else None))
    ident = identity_for(current_user)
    problem = eligibility_problem(ident, cfg) or _schedule_problem(schedule)
    if problem:
        flash(problem, "danger")
        return redirect(back)
    try:
        row = store.release_shift(
            schedule["id"], request.form.get("shift_id", "").strip(),
            aliases=ident["aliases"], saved_by=current_user.email,
            sources=None if cfg["allow_release_assigned"] else ("signup",),
            not_before=local_now() + timedelta(hours=cfg["release_cutoff_hours"]),
        )
    except store.SignupConflict as exc:
        flash(f"Could not remove you: {exc}", "danger")
        return redirect(back)
    log.info("signup: %s released %s in schedule %s", current_user.email, row["shift_id"], schedule["id"])
    flash(f"You were removed from {row['shift_name'] or row['shift_id']} on {row['date']}; "
          "the shift is open again.", "success")
    return redirect(back)


@bp.route("/signup/schedules/<int:schedule_id>/toggle", methods=["POST"])
@admin_required
def signup_toggle(schedule_id: int):
    schedule = store.get_schedule(schedule_id)
    back = _safe_next(url_for("main.schedules_page"))
    if schedule is None:
        flash("Schedule not found.", "danger")
        return redirect(back)
    is_open = request.form.get("open") == "1"
    store.set_signup_open(schedule_id, is_open)
    flash(f"Schedule '{schedule['name']}' is now {'open' if is_open else 'closed'} to self-signup.", "success")
    return redirect(back)
