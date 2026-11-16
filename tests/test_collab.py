"""Collaboration directory sync: client, sync, matching, integration points.

All HTTP is mocked by patching urllib.request.urlopen inside app.collab; no
test reaches the network.
"""
import io
import json
import sqlite3
import urllib.error
import urllib.parse

import pytest

from app import collab, store

BASE = "https://collab.test"
TOKEN = "mu2e_testtoken_not_real"


def _user(uid, display, *, member=True, active=True, contact_email="", email="",
          fnal="", inst="Fermilab", number=None, version=1, updated="2026-11-01T00:00:00+00:00"):
    return {
        "schema_version": version, "id": uid, "member_number": number if number is not None else str(uid),
        "display_name": display, "username": f"u{uid}", "email": email, "contact_email": contact_email,
        "role": "member", "status": "", "is_active": active, "is_collaboration_member": member,
        "institution": {"id": 1, "name": inst, "collaboration_code": "FNAL"} if inst else None,
        "collaboration_position": "", "orcid": "", "inspire_id": "", "fnal_username": fnal,
        "github_username": "", "updated_at": updated,
    }


class FakeAPI:
    """Routes GETs to canned JSON; records every request URL and header."""

    def __init__(self, users, *, admin=True, phones=None, fail=None, page_size=2):
        self.users = users
        self.admin = admin
        self.phones = phones or {}
        self.fail = fail or {}          # path-prefix -> (status, error)
        self.page_size = page_size
        self.calls = []

    def __call__(self, req, timeout=None):
        url = urllib.parse.urlparse(req.full_url)
        params = dict(urllib.parse.parse_qsl(url.query))
        self.calls.append((url.path, params, req.get_header("Authorization")))
        path = url.path
        for prefix, (status, message) in self.fail.items():
            if path.startswith(prefix):
                raise urllib.error.HTTPError(req.full_url, status, message, {},
                                             io.BytesIO(json.dumps({"error": message}).encode()))
        if path == "/api/v1/whoami/":
            body = {"user": {"username": "svc"}, "permissions": {"is_admin": self.admin},
                    "token": {"mode": "read-only", "expires_at": None}}
        elif path == "/api/v1/users/":
            users = self.users
            if params.get("updated_since"):
                users = [u for u in users if u["updated_at"] > params["updated_since"]]
            page = int(params.get("page", 1))
            chunk = users[(page - 1) * self.page_size: page * self.page_size]
            num_pages = max(1, -(-len(users) // self.page_size))
            body = {"schema_version": 1, "count": len(users), "page": page, "page_size": self.page_size,
                    "num_pages": num_pages, "previous": None, "results": chunk,
                    "next": f"/api/v1/users/?page={page + 1}" if page < num_pages else None}
        elif path.startswith("/api/v1/users/") and params.get("detail") == "full":
            uid = int(path.rstrip("/").split("/")[-1])
            office, mobile = self.phones.get(uid, ("", ""))
            body = {"id": uid, "profile": {"office_phone": office, "mobile_phone": mobile}}
        else:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(body).encode())


USERS = [
    _user(1, "Norman, Andrew J.", contact_email="anorman@fnal.gov", fnal="anorman"),
    _user(2, "Byrum, Karen", contact_email="kbyrum@anl.gov", inst="Argonne"),
    _user(3, "Gone, Former", active=False, contact_email="gone@x.edu"),
    _user(4, "Visitor, Vera", member=False, contact_email="vera@x.edu"),
    _user(5, "Doe, Jane", contact_email="jdoe1@x.edu"),
    _user(6, "Doe, Jane Q.", contact_email="jdoe2@y.edu"),  # ambiguous name key
]


@pytest.fixture()
def cfg(app):
    c = collab.resolve_config(overrides={"base_url": BASE, "token": TOKEN})
    app.config["COLLAB"] = c
    return c


@pytest.fixture()
def fake(monkeypatch):
    api = FakeAPI(USERS, phones={1: ("+1 630 840 0001", "+1 630 555 0001")})
    monkeypatch.setattr(collab.urllib.request, "urlopen", api)
    monkeypatch.setattr(collab.time, "sleep", lambda s: None)
    return api


def _sync(app, cfg, **kw):
    with app.app_context():
        return collab.sync(cfg, **kw)


# -- configuration --------------------------------------------------------

def test_config_precedence(tmp_path):
    token_file = tmp_path / "tok"
    token_file.write_text("mu2e_fromfile\n")
    env = {"MU2E_COLLAB_SYNC_HOURS": "2", "MU2E_COLLAB_TOKEN_FILE": str(token_file),
           "MU2E_COLLAB_FETCH_PHONES": "0"}
    c = collab.resolve_config({"sync_hours": 12, "base_url": "https://yaml.example/", "fetch_phones": True},
                              overrides={"sync_hours": 1}, environ=env)
    assert c["sync_hours"] == 1.0                     # command line beats env
    assert c["fetch_phones"] is False                 # env beats yaml
    assert c["base_url"] == "https://yaml.example"    # yaml beats default, slash trimmed
    assert c["token"] == "mu2e_fromfile" and c["enabled"]
    pub = collab.public_config(c)
    assert "token" not in pub and pub["token_set"] is True


def test_disabled_without_token():
    assert collab.resolve_config(environ={})["enabled"] is False


def test_client_repr_hides_token():
    assert TOKEN not in repr(collab.Client(BASE, TOKEN))


# -- name handling ----------------------------------------------------------

@pytest.mark.parametrize("raw,name,key", [
    ("Norman, Andrew J.", "Andrew J. Norman", "andrew norman"),
    ("Andrew Norman", "Andrew Norman", "andrew norman"),
    ("O'Neil, Mary-Kate", "Mary-Kate O'Neil", "marykate oneil"),
    ("Cher", "Cher", "cher"),
])
def test_name_normalization(raw, name, key):
    assert store.collab_display_to_name(raw) == name
    assert store.person_match_key(raw) == key


# -- sync ------------------------------------------------------------------

def test_full_sync_paginates_and_fetches_phones(app, cfg, fake):
    status = _sync(app, cfg)
    assert status["ok"], status
    assert status["fetched"] == 6 and status["phone_lookups"] == 6
    user_pages = [c for c in fake.calls if c[0] == "/api/v1/users/"]
    assert len(user_pages) == 3                       # followed `next` across pages
    assert all(c[2] == f"Bearer {TOKEN}" for c in fake.calls)
    with app.app_context():
        rows = {r["collab_id"]: r for r in store.list_collab_members()}
        assert rows[1]["name"] == "Andrew J. Norman"
        assert rows[1]["office_phone"] == "+1 630 840 0001"
        assert rows[2]["institution"] == "Argonne"
        assert store.get_setting(store.SETTING_COLLAB_SINCE) == status["started_at"]
        assert store.get_collab_status()["ok"] is True


def test_incremental_sync_uses_updated_since(app, cfg, fake):
    _sync(app, cfg)
    fake.calls.clear()
    fake.users = USERS + [_user(7, "New, Person", updated="2099-01-01T00:00:00+00:00")]
    status = _sync(app, cfg)
    first = next(c for c in fake.calls if c[0] == "/api/v1/users/")
    assert "updated_since" in first[1]
    assert status["fetched"] == 1
    with app.app_context():
        assert store.collab_member_counts()["total"] == 7


def test_full_resync_ignores_updated_since(app, cfg, fake):
    _sync(app, cfg)
    fake.calls.clear()
    _sync(app, cfg, full=True)
    first = next(c for c in fake.calls if c[0] == "/api/v1/users/")
    assert "updated_since" not in first[1]


def test_schema_version_2_rejected_and_nothing_written(app, cfg, fake):
    fake.users = [_user(1, "Future, Record", version=2)]
    status = _sync(app, cfg)
    assert not status["ok"] and "schema_version 2" in status["error"]
    with app.app_context():
        assert store.collab_member_counts()["total"] == 0
        assert store.get_setting(store.SETTING_COLLAB_SINCE) is None


def test_401_is_reported_not_retried(app, cfg, fake):
    fake.fail = {"/api/v1/whoami/": (401, "Token revoked")}
    status = _sync(app, cfg)
    assert not status["ok"] and status["http_status"] == 401
    assert "Token revoked" in status["error"]
    assert TOKEN not in json.dumps(status)
    assert len([c for c in fake.calls if c[0] == "/api/v1/whoami/"]) == 1


def test_5xx_is_retried(app, cfg, monkeypatch, fake):
    real = FakeAPI.__call__
    state = {"n": 0}

    def flaky(self, req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b"{}"))
        return real(self, req, timeout)

    monkeypatch.setattr(FakeAPI, "__call__", flaky)
    assert _sync(app, cfg)["ok"]


def test_failed_sync_keeps_last_success(app, cfg, fake):
    _sync(app, cfg)
    fake.fail = {"/api/v1/whoami/": (500, "down")}
    status = _sync(app, cfg)
    assert not status["ok"] and status["last_success_at"]


def test_non_admin_token_skips_phones(app, cfg, fake):
    fake.admin = False
    status = _sync(app, cfg)
    assert status["ok"] and status["phone_lookups"] == 0 and "not an admin" in status["warning"]


def test_dry_run_writes_nothing(app, cfg, fake):
    assert _sync(app, cfg, dry_run=True)["fetched"] == 6
    with app.app_context():
        assert store.collab_member_counts()["total"] == 0


def test_lease_blocks_second_worker(app, cfg, fake):
    with app.app_context():
        assert store.acquire_collab_lease("worker-a", ttl_seconds=600)
    assert collab.sync_with_lease(app) is None
    with app.app_context():
        store.release_collab_lease("worker-a")
    assert collab.sync_with_lease(app)["ok"]
    # min interval: a fresh success suppresses the periodic run
    assert collab.sync_with_lease(app, min_interval_seconds=3600) is None


# -- matching and integration ----------------------------------------------

def test_find_member_by_email_username_and_name(app, cfg, fake):
    _sync(app, cfg)
    with app.app_context():
        assert store.find_collab_member(emails=["KBYRUM@anl.gov"])["collab_id"] == 2
        assert store.find_collab_member("Somebody", emails=["anorman@fnal.gov"])["collab_id"] == 1
        assert store.find_collab_member("", emails=["nomatch@fnal.gov"], fnal_username="anorman")["collab_id"] == 1
        assert store.find_collab_member("Andrew Norman")["collab_id"] == 1
        assert store.find_collab_member("Jane Doe") is None          # ambiguous
        assert store.find_collab_member("Nobody Here") is None


def test_people_suggestions_include_assignable_members_once(app, cfg, fake, user_client):
    _sync(app, cfg)
    with app.app_context():
        store.upsert_contact("Andrew Norman", email="anorman@fnal.gov")
        names = store.list_people_names()
    assert "Karen Byrum" in names
    assert "Andrew Norman" in names and "Andrew J. Norman" not in names   # no duplicate
    assert "Former Gone" not in names          # inactive
    assert "Vera Visitor" not in names         # non-member (members_only default)
    app.config["COLLAB"]["members_only"] = False
    with app.app_context():
        assert "Vera Visitor" in store.list_people_names()


def test_contact_fills_blanks_but_local_wins(app, cfg, fake):
    _sync(app, cfg)
    with app.app_context():
        c = store.get_contact("Karen Byrum")                     # collab only
        assert c["email"] == "kbyrum@anl.gov" and c["institution"] == "Argonne"
        assert c["source"] == "collaboration"
        store.upsert_contact("Andrew Norman", email="me@home.example", phone="x1234")
        c = store.get_contact("Andrew Norman")
        assert c["email"] == "me@home.example" and c["phone"] == "x1234"   # local wins
        assert c["institution"] == "Fermilab"                               # blank filled
        assert store.resolve_reminder_email("Karen Byrum") == "kbyrum@anl.gov"


def test_profile_shows_linked_collab_record(app, cfg, fake, user_client):
    fake.users = USERS + [_user(9, "Shifter, Regular", contact_email="shifter@example.gov")]
    _sync(app, cfg)
    html = user_client.get("/profile").get_data(as_text=True)
    assert 'id="collab-record"' in html and "Shifter, Regular" in html


def test_admin_page_and_sync_button(app, cfg, fake, admin_client):
    html = admin_client.get("/admin/collaboration").get_data(as_text=True)
    assert "Collaboration Directory" in html and TOKEN not in html
    resp = admin_client.post("/admin/collaboration/sync", data={"full": "1"}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Collaboration sync complete" in html
    assert "Karen Byrum" in html and TOKEN not in html


def test_admin_sync_requires_token(app, admin_client):
    app.config["COLLAB"] = collab.resolve_config(environ={})
    resp = admin_client.post("/admin/collaboration/sync", follow_redirects=True)
    assert "No collaboration API token" in resp.get_data(as_text=True)


def test_collab_pages_require_admin(user_client):
    assert user_client.get("/admin/collaboration").status_code == 302
    assert user_client.post("/admin/collaboration/sync").status_code == 302


def test_cli_status_and_sync(app, monkeypatch, tmp_path, capsys, fake):
    import cli

    token_file = tmp_path / "tok"
    token_file.write_text(TOKEN)
    monkeypatch.setattr("sys.argv", ["mu2e-shift-scheduler", "collab-sync", "--base-url", BASE,
                                     "--token-file", str(token_file), "--full"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("OK: 6 record(s)") and TOKEN not in out
    with sqlite3.connect(app.config["APP_DB_PATH"]) as conn:
        assert conn.execute("SELECT count(*) FROM collab_members").fetchone()[0] == 6

    monkeypatch.setattr("sys.argv", ["mu2e-shift-scheduler", "collab-sync", "--status",
                                     "--token-file", str(token_file)])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert '"token_set": true' in out and TOKEN not in out
