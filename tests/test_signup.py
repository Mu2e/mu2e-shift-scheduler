"""Shift self-signup: claim / block claim / release, guards, identity, UI."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app import signup, store

D = date.today() + timedelta(days=30)          # a future block (Mon..Thu style)
P = date.today() - timedelta(days=10)          # a past shift


def _iso(d):
    return d.isoformat()


BLOCK = [  # one Shift Setup block occurrence: 3 shifts sharing week/block/date range
    {"shift_id": "w1-b1-day", "shift_name": "Day", "week": 1, "block_number": 1, "block_name": "Weekdays",
     "date": _iso(D), "date_end": _iso(D + timedelta(days=3)), "start_time": "08:00", "end_time": "16:00"},
    {"shift_id": "w1-b1-swing", "shift_name": "Swing", "week": 1, "block_number": 1, "block_name": "Weekdays",
     "date": _iso(D), "date_end": _iso(D + timedelta(days=3)), "start_time": "16:00", "end_time": "00:00"},
    {"shift_id": "w1-b1-owl", "shift_name": "Owl", "week": 1, "block_number": 1, "block_name": "Weekdays",
     "date": _iso(D), "date_end": _iso(D + timedelta(days=3)), "start_time": "00:00", "end_time": "08:00"},
]
OTHER = [
    # different block, same days and hours as the Day shift -> overlaps it
    {"shift_id": "w1-b2-day", "shift_name": "Day", "week": 1, "block_number": 2, "block_name": "Extra",
     "date": _iso(D + timedelta(days=1)), "date_end": "", "start_time": "09:00", "end_time": "10:00"},
    {"shift_id": "past", "shift_name": "Old", "date": _iso(P), "date_end": "", "start_time": "08:00", "end_time": "16:00"},
    {"shift_id": "later", "shift_name": "Later", "date": _iso(D + timedelta(days=20)), "date_end": "",
     "start_time": "08:00", "end_time": "16:00"},
]


@pytest.fixture()
def sched(app):
    with app.app_context():
        cid = next(c["id"] for c in store.list_classifications() if c["name"] == "General Shifts")
        sid, _ = store.upsert_schedule("Run 1", cid, BLOCK + OTHER, source="upload")
    return sid


def _assignment(app, sid, shift_id):
    with app.app_context():
        return store.get_assignments(sid).get(shift_id)


def _claim(client, sid, shift_id, scope="shift", **extra):
    return client.post("/signup/claim", data={"schedule_id": sid, "shift_id": shift_id, "scope": scope, **extra},
                       follow_redirects=True)


def _release(client, sid, shift_id):
    return client.post("/signup/release", data={"schedule_id": sid, "shift_id": shift_id}, follow_redirects=True)


def _add_user(app, email, name):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
        uid = conn.execute(
            "INSERT INTO users (email, name, role, auth_provider, created_at, updated_at) "
            "VALUES (?, ?, 'user', 'oidc', ?, ?)", (email, name, now, now)).lastrowid
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return c


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def test_claim_open_shift(user_client, app, sched):
    html = _claim(user_client, sched, "later").get_data(as_text=True)
    assert "You are signed up for Later" in html
    row = _assignment(app, sched, "later")
    assert row["person"] == "Regular Shifter"
    assert row["source"] == "signup"
    assert row["saved_by"] == "shifter@example.gov"
    with app.app_context():
        log = store.recent_signup_log()
    assert log[0]["action"] == "signup" and log[0]["shift_id"] == "later"


def test_claim_refuses_assigned_shift(user_client, app, sched):
    with app.app_context():
        store.save_assignments(sched, [{"shift_id": "later", "person": "Alice"}], saved_by="x")
    html = _claim(user_client, sched, "later").get_data(as_text=True)
    assert "Already assigned" in html and "Alice" in html
    assert _assignment(app, sched, "later")["person"] == "Alice"


def test_unassigned_marker_counts_as_open(user_client, app, sched):
    with app.app_context():
        store.save_assignments(sched, [{"shift_id": "later", "person": "UNASSIGNED"}], saved_by="x")
    _claim(user_client, sched, "later")
    assert _assignment(app, sched, "later")["person"] == "Regular Shifter"


def test_block_claim_takes_every_shift(user_client, app, sched):
    html = _claim(user_client, sched, "w1-b1-swing", scope="block").get_data(as_text=True)
    assert "signed up for 3 shifts" in html
    for sid in ("w1-b1-day", "w1-b1-swing", "w1-b1-owl"):
        assert _assignment(app, sched, sid)["person"] == "Regular Shifter"


def test_block_claim_is_all_or_nothing(user_client, app, sched):
    with app.app_context():
        store.save_assignments(sched, [{"shift_id": "w1-b1-owl", "person": "Alice"}], saved_by="x")
    html = _claim(user_client, sched, "w1-b1-day", scope="block").get_data(as_text=True)
    assert "Already assigned" in html
    assert _assignment(app, sched, "w1-b1-day") is None
    assert _assignment(app, sched, "w1-b1-swing") is None


def test_block_claim_can_be_disabled(user_client, app, sched):
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "allow_block_signup": False}
    html = _claim(user_client, sched, "w1-b1-day", scope="block").get_data(as_text=True)
    assert "Block sign-up is disabled" in html
    assert _assignment(app, sched, "w1-b1-day") is None


def test_claim_refuses_overlap_with_held_shift(user_client, app, sched):
    _claim(user_client, sched, "w1-b1-day")
    html = _claim(user_client, sched, "w1-b2-day").get_data(as_text=True)
    assert "overlaps" in html
    assert _assignment(app, sched, "w1-b2-day") is None


def test_claim_refuses_past_and_cutoff(user_client, app, sched):
    assert "already started" in _claim(user_client, sched, "past").get_data(as_text=True)
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "signup_cutoff_hours": 24 * 365}
    assert "cutoff" in _claim(user_client, sched, "later").get_data(as_text=True)
    assert _assignment(app, sched, "later") is None


def test_second_claimant_loses(app, sched):
    with app.app_context():
        store.claim_shifts(sched, ["later"], "A", institution="", aliases=set(), saved_by="a")
        with pytest.raises(store.SignupConflict):
            store.claim_shifts(sched, ["later"], "B", institution="", aliases=set(), saved_by="b")
        assert store.get_assignments(sched)["later"]["person"] == "A"


def test_unknown_shift_and_schedule(user_client, sched):
    assert "does not exist" in _claim(user_client, sched, "nope").get_data(as_text=True)
    assert "Choose a stored schedule" in _claim(user_client, 99999, "later").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------

def test_release_own_signup(user_client, app, sched):
    _claim(user_client, sched, "later")
    html = _release(user_client, sched, "later").get_data(as_text=True)
    assert "shift is open again" in html
    row = _assignment(app, sched, "later")
    assert row["person"] == "" and row["source"] == ""
    with app.app_context():
        assert [e["action"] for e in store.recent_signup_log()][:2] == ["release", "signup"]


def test_release_someone_elses_shift_refused(user_client, app, sched):
    other = _add_user(app, "other@example.gov", "Other Person")
    _claim(other, sched, "later")
    assert "not assigned to" in _release(user_client, sched, "later").get_data(as_text=True)
    assert _assignment(app, sched, "later")["person"] == "Other Person"


def test_release_solver_assignment_needs_config(user_client, app, sched):
    with app.app_context():
        store.save_assignments(sched, [{"shift_id": "later", "person": "Regular Shifter"}], saved_by="solver")
    assert "ask a run coordinator" in _release(user_client, sched, "later").get_data(as_text=True)
    assert _assignment(app, sched, "later")["person"] == "Regular Shifter"
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "allow_release_assigned": True}
    assert "open again" in _release(user_client, sched, "later").get_data(as_text=True)


def test_release_cutoff(user_client, app, sched):
    _claim(user_client, sched, "later")
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "release_cutoff_hours": 24 * 365}
    assert "release cutoff" in _release(user_client, sched, "later").get_data(as_text=True)
    assert _assignment(app, sched, "later")["person"] == "Regular Shifter"


def test_admin_assign_records_source(admin_client, app, sched):
    admin_client.post("/calendar/assign", data={"schedule_id": sched, "shift_id": "later", "person": "Regular Shifter"})
    assert _assignment(app, sched, "later")["source"] == "admin"


# ---------------------------------------------------------------------------
# Schedule / instance switches, identity
# ---------------------------------------------------------------------------

def test_closed_schedule_and_admin_toggle(user_client, admin_client, app, sched):
    # shifters cannot toggle
    user_client.post(f"/signup/schedules/{sched}/toggle", data={"open": "0"})
    with app.app_context():
        assert store.get_schedule(sched)["signup_open"] == 1
    admin_client.post(f"/signup/schedules/{sched}/toggle", data={"open": "0"})
    with app.app_context():
        assert store.get_schedule(sched)["signup_open"] == 0
    assert "closed to self-signup" in _claim(user_client, sched, "later").get_data(as_text=True)
    assert _assignment(app, sched, "later") is None
    # calendar offers no signup menu on a closed schedule
    html = user_client.get(f"/calendar?schedule=Run+1&date={_iso(D)}").get_data(as_text=True)
    assert "signupMenuClaim" not in html


def test_disabled_instance(user_client, app, sched):
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "enabled": False}
    assert "disabled" in _claim(user_client, sched, "later").get_data(as_text=True)
    html = user_client.get("/calendar").get_data(as_text=True)
    assert 'href="/signup"' not in html


def test_identity_filled_from_collaboration_directory(user_client, app, sched):
    with app.app_context():
        store.upsert_collab_members([{
            "collab_id": 7, "member_number": "M7", "display_name": "Shifter, Reggie", "name": "Reggie Shifter",
            "match_key": "reggie shifter", "email": "shifter@example.gov", "contact_email": "",
            "fnal_username": "", "orcid": "", "institution": "Fermilab", "collaboration_position": "",
            "office_phone": "630-840-0000", "mobile_phone": "", "is_active": 1,
            "is_collaboration_member": 1, "collab_updated_at": "",
        }])
        # Solver assigned the directory spelling; the account is "Regular Shifter".
        store.save_assignments(sched, [{"shift_id": "w1-b2-day", "person": "Reggie Shifter"}], saved_by="solver")
    _claim(user_client, sched, "later")
    row = _assignment(app, sched, "later")
    assert row["person"] == "Regular Shifter" and row["institution"] == "Fermilab"
    with app.app_context():
        assert store.get_contact("Regular Shifter")["phone"] == "630-840-0000"
    # The directory alias makes the solver assignment "mine".
    html = user_client.get("/signup?schedule=Run+1&show=mine").get_data(as_text=True)
    assert "Extra" in html                       # the solver-assigned block shows under "mine"
    assert html.count(">you</span>") == 2        # both held shifts are marked as the user's
    assert "member #M7" in html
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "allow_release_assigned": True}
    assert "open again" in _release(user_client, sched, "w1-b2-day").get_data(as_text=True)


def test_require_collab_member(user_client, app, sched):
    app.config["SIGNUP"] = {**app.config["SIGNUP"], "require_collab_member": True}
    assert "Collaboration member" in _claim(user_client, sched, "later").get_data(as_text=True)


def test_open_redirect_rejected(user_client, sched):
    resp = user_client.post("/signup/claim", data={"schedule_id": sched, "shift_id": "later",
                                                   "next": "//evil.example/x"})
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]
    resp = user_client.post("/signup/claim", data={"schedule_id": sched, "shift_id": "w1-b1-day",
                                                   "next": "/calendar?schedule=Run+1"})
    assert resp.headers["Location"].endswith("/calendar?schedule=Run+1")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def test_calendar_context_menu_for_shifter(user_client, app, sched):
    html = user_client.get(f"/calendar?schedule=Run+1&view=week&date={_iso(D)}").get_data(as_text=True)
    assert 'id="signupMenuClaim"' in html
    assert 'data-can-claim="1"' in html
    assert 'data-can-claim-block="1"' in html and 'data-block-size="3"' in html
    assert 'id="assignMenuAssign"' not in html          # no admin items
    _claim(user_client, sched, "w1-b1-day")
    html = user_client.get(f"/calendar?schedule=Run+1&view=week&date={_iso(D)}").get_data(as_text=True)
    assert 'data-mine="1"' in html and 'data-can-release="1"' in html
    assert 'data-can-claim-block="1"' not in html       # block no longer fully open


def test_calendar_context_menu_for_admin_has_both(admin_client, sched):
    html = admin_client.get(f"/calendar?schedule=Run+1&view=week&date={_iso(D)}").get_data(as_text=True)
    assert 'id="signupMenuClaim"' in html and 'id="assignMenuAssign"' in html


def test_signup_page_views(user_client, admin_client, app, sched):
    html = user_client.get("/signup?schedule=Run+1").get_data(as_text=True)
    assert "Shift Sign-Up" in html and "Weekdays" in html
    assert "Sign up for whole block (3 shifts)" in html
    assert "Old" not in html                     # past hidden by default
    assert "Recent self-signup activity" not in html
    assert "Old" in user_client.get("/signup?schedule=Run+1&show=all&past=1").get_data(as_text=True)
    _claim(user_client, sched, "later")
    html = user_client.get("/signup?schedule=Run+1&show=mine").get_data(as_text=True)
    assert "My upcoming shifts" in html and "Remove me" in html
    assert "Recent self-signup activity" in admin_client.get("/signup?schedule=Run+1").get_data(as_text=True)


def test_nav_and_theme(user_client):
    html = user_client.get("/signup").get_data(as_text=True)
    assert 'href="/signup"' in html
    assert "navbar-mu2e" in html and "css/mu2e-theme.css" in html


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_config_precedence():
    cfg = signup.resolve_config(
        {"enabled": False, "release_cutoff_hours": 24, "signup_cutoff_hours": 2},
        overrides={"signup_cutoff_hours": 6, "allow_block_signup": None},
        environ={"MU2E_SIGNUP_ENABLED": "1", "MU2E_SIGNUP_RELEASE_CUTOFF_HOURS": "48"},
    )
    assert cfg["enabled"] is True                 # env beats yaml
    assert cfg["release_cutoff_hours"] == 48.0    # env beats yaml
    assert cfg["signup_cutoff_hours"] == 6.0      # CLI beats yaml
    assert cfg["allow_block_signup"] is True      # None override leaves default
    assert signup.resolve_config(environ={})["allow_release_assigned"] is False


def test_cli_serve_flags():
    from cli import build_parser

    args = build_parser().parse_args(["serve", "--no-signup", "--signup-release-cutoff-hours", "12"])
    assert args.signup_enabled is False and args.signup_release_cutoff_hours == 12.0
    assert args.signup_allow_block is None
