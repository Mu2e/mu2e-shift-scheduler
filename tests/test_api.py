"""Token-authenticated /api/v1: auth gate, token lifecycle, and on-duty logic."""
import io

from app import store

BLOCK_CSV = (
    "shift_id,schedule_name,week,block_number,block_name,block_type,days,date,date_end,start_time,end_time,shift_type,points\n"
    "rc-day,RC,1,1,Day,day,,2026-05-04,,08:00,16:00,Day,1.0\n"
    "rc-night,RC,1,2,Night,night,,2026-05-04,,20:00,04:00,Night,2.0\n"
    "rc-week,RC,1,3,Week,day,\"Mon,Tue,Wed\",2026-05-11,2026-05-17,00:00,23:59,Week,1.0\n"
)


def _mint(app, label="test"):
    with app.app_context():
        _tid, token = store.create_api_token(label, "admin@example.gov")
    return token


def _save_schedule(admin_client, name="RC", classification="run-coordinators"):
    return admin_client.post(
        "/schedules/save",
        data={"name": name, "classification": classification,
              "csv_file": (io.BytesIO(BLOCK_CSV.encode()), "rc.csv")},
        content_type="multipart/form-data",
    )


def _assign(app, name, shift_id, person):
    with app.app_context():
        sid = store.get_schedule_by_name(name)["id"]
        store.assign_person(sid, shift_id, person, "admin@example.gov")


# ── auth gate ────────────────────────────────────────────────────────────────

def test_health_needs_no_token(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_missing_token_is_401_json_not_redirect(client):
    resp = client.get("/api/v1/on-duty")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "missing bearer token"


def test_bogus_token_is_401(client):
    resp = client.get("/api/v1/on-duty", headers={"Authorization": "Bearer mu2e_nope"})
    assert resp.status_code == 401


def test_logged_in_session_alone_does_not_grant_api(admin_client):
    resp = admin_client.get("/api/v1/classifications")
    assert resp.status_code == 401


def test_valid_token_grants_access_and_stamps_last_used(client, app):
    token = _mint(app)
    resp = client.get("/api/v1/classifications", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = {c["slug"] for c in resp.get_json()["classifications"]}
    assert {"general-shifts", "run-coordinators", "oncall-daq-experts", "oncall-cryo-experts"} <= slugs
    with app.app_context():
        assert store.list_api_tokens()[0]["last_used_at"] is not None


def test_x_api_token_header_also_accepted(client, app):
    token = _mint(app)
    assert client.get("/api/v1/schedules", headers={"X-API-Token": token}).status_code == 200


def test_revoked_token_is_rejected(client, app):
    token = _mint(app)
    with app.app_context():
        tid = store.list_api_tokens()[0]["id"]
        assert store.revoke_api_token(tid) is True
        assert store.revoke_api_token(tid) is False  # already revoked
    resp = client.get("/api/v1/schedules", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_only_a_hash_is_stored(app):
    token = _mint(app)
    with app.app_context():
        with store.connect() as conn:
            row = conn.execute("SELECT token_hash, prefix FROM api_tokens").fetchone()
    assert token not in row["token_hash"]
    assert token.startswith(row["prefix"])


def test_label_is_required(app):
    import pytest
    with app.app_context():
        with pytest.raises(ValueError):
            store.create_api_token("   ", "x")


# ── admin page ───────────────────────────────────────────────────────────────

def test_admin_page_creates_and_shows_token_once(admin_client, app):
    resp = admin_client.post("/admin/api-tokens/create", data={"label": "board"}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "shown once" in html
    assert "mu2e_" in html
    # Second load: plaintext gone, row remains
    html = admin_client.get("/admin/api-tokens").get_data(as_text=True)
    assert "shown once" not in html
    assert "board" in html
    assert "Active" in html


def test_admin_page_create_without_label_fails_cleanly(admin_client):
    resp = admin_client.post("/admin/api-tokens/create", data={"label": ""}, follow_redirects=True)
    assert resp.status_code == 200
    assert "label is required" in resp.get_data(as_text=True)


def test_admin_page_revoke(admin_client, app):
    admin_client.post("/admin/api-tokens/create", data={"label": "temp"})
    with app.app_context():
        tid = store.list_api_tokens()[0]["id"]
    resp = admin_client.post(f"/admin/api-tokens/{tid}/revoke", follow_redirects=True)
    assert "Revoked" in resp.get_data(as_text=True)


def test_non_admin_cannot_reach_token_page(user_client):
    resp = user_client.get("/admin/api-tokens")
    assert resp.status_code == 302  # bounced to welcome with a flash


# ── on-duty logic ────────────────────────────────────────────────────────────

def _on_duty(client, token, at):
    resp = client.get(f"/api/v1/on-duty?at={at}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_on_duty_day_shift_with_contact(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-day", "Alice")
    with app.app_context():
        store.upsert_contact("Alice", email="alice@fnal.gov", phone="+1 630 555 1111", institution="Fermilab")
    token = _mint(app)
    body = _on_duty(client, token, "2026-05-04T10:00")
    assert body["timezone"] == "America/Chicago"
    assert len(body["on_duty"]) == 1
    hit = body["on_duty"][0]
    assert hit["classification_slug"] == "run-coordinators"
    assert hit["shift_name"] == "Day"
    assert hit["person"] == {"name": "Alice", "email": "alice@fnal.gov",
                             "phone": "+1 630 555 1111", "institution": "Fermilab"}
    assert hit["starts_at"] == "2026-05-04T08:00"
    assert hit["ends_at"] == "2026-05-04T16:00"


def test_on_duty_boundaries_are_half_open(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-day", "Alice")
    token = _mint(app)
    assert len(_on_duty(client, token, "2026-05-04T08:00")["on_duty"]) == 1
    assert len(_on_duty(client, token, "2026-05-04T16:00")["on_duty"]) == 0
    assert len(_on_duty(client, token, "2026-05-04T07:59")["on_duty"]) == 0


def test_on_duty_night_shift_wraps_midnight(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-night", "Bob")
    token = _mint(app)
    assert _on_duty(client, token, "2026-05-04T23:30")["on_duty"][0]["person"]["name"] == "Bob"
    late = _on_duty(client, token, "2026-05-05T03:00")["on_duty"]
    assert late and late[0]["person"]["name"] == "Bob"
    assert late[0]["ends_at"] == "2026-05-05T04:00"
    assert _on_duty(client, token, "2026-05-05T04:00")["on_duty"] == []


def test_on_duty_multi_day_block_honours_weekday_filter(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-week", "Carol")
    token = _mint(app)
    # 2026-05-12 is a Tuesday (in Mon,Tue,Wed); 2026-05-14 is a Thursday (not)
    assert _on_duty(client, token, "2026-05-12T12:00")["on_duty"][0]["person"]["name"] == "Carol"
    assert _on_duty(client, token, "2026-05-14T12:00")["on_duty"] == []


def test_on_duty_unassigned_and_empty_are_skipped(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-day", "UNASSIGNED")
    token = _mint(app)
    assert _on_duty(client, token, "2026-05-04T10:00")["on_duty"] == []


def test_on_duty_classification_filter(admin_client, client, app):
    _save_schedule(admin_client, name="RC", classification="run-coordinators")
    _save_schedule(admin_client, name="DAQ", classification="oncall-daq-experts")
    _assign(app, "RC", "rc-day", "Alice")
    _assign(app, "DAQ", "rc-day", "Dave")
    token = _mint(app)
    both = _on_duty(client, token, "2026-05-04T10:00")["on_duty"]
    assert [h["person"]["name"] for h in both] == ["Alice", "Dave"]  # taxonomy order
    only = client.get(
        "/api/v1/on-duty?at=2026-05-04T10:00&classification=oncall-daq-experts",
        headers={"Authorization": f"Bearer {token}"},
    ).get_json()["on_duty"]
    assert [h["person"]["name"] for h in only] == ["Dave"]


def test_on_duty_accepts_aware_timestamps(admin_client, client, app):
    _save_schedule(admin_client)
    _assign(app, "RC", "rc-day", "Alice")
    token = _mint(app)
    # 15:00Z in May is 10:00 CDT
    body = _on_duty(client, token, "2026-05-04T15:00:00Z")
    assert body["at"].startswith("2026-05-04T10:00")
    assert len(body["on_duty"]) == 1


def test_on_duty_bad_at_is_400(client, app):
    token = _mint(app)
    resp = client.get("/api/v1/on-duty?at=yesterday", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


def test_on_duty_defaults_to_now(client, app):
    token = _mint(app)
    body = _on_duty(client, token, "")
    assert body["on_duty"] == []
    assert body["at"]
