"""Tests for app.store (SQLite persistence)."""
import pytest

from app import store


def _sample_shifts(prefix="s", n=2):
    return [
        {
            "shift_id": f"{prefix}{i}",
            "shift_type": "Day",
            "days": "Mon,Tue,Wed,Thu",
            "date": f"2026-05-0{i + 3}",
            "date_end": f"2026-05-0{i + 6}",
            "start_time": "08:00",
            "end_time": "16:00",
            "points": "1.5",
        }
        for i in range(1, n + 1)
    ]


def test_init_seeds_default_classifications(app):
    with app.app_context():
        names = [c["name"] for c in store.list_classifications()]
    assert names == ["General Shifts", "Run Coordinators", "Oncall DAQ Experts"]
    # Re-init is idempotent
    store.init_app_db(app)
    with app.app_context():
        assert len(store.list_classifications()) == 3


def test_classification_crud(app):
    with app.app_context():
        cid = store.create_classification("Shield Experts")
        assert store.get_classification(cid)["slug"] == "shield-experts"
        with pytest.raises(ValueError, match="already exists"):
            store.create_classification("shield experts")  # NOCASE duplicate
        store.update_classification(cid, show_tab=False)
        assert store.get_classification(cid)["show_tab"] == 0
        assert all(c["id"] != cid for c in store.list_classifications(visible_only=True))
        store.delete_classification(cid)
        assert store.get_classification(cid) is None


def test_delete_classification_blocked_when_used(app):
    with app.app_context():
        cid = store.create_classification("In Use")
        store.upsert_schedule("Fall 2026", cid, _sample_shifts(), source="upload")
        with pytest.raises(ValueError, match="Cannot delete"):
            store.delete_classification(cid)


def test_upsert_schedule_overwrites_by_name_case_insensitive(app):
    with app.app_context():
        cid = store.create_classification("Test Class")
        sid1, dropped = store.upsert_schedule(
            "Fall 2026", cid, _sample_shifts(n=2), source="upload", created_by="a@b.gov"
        )
        assert dropped == 0
        # Save an assignment for s1 and one for s2
        store.save_assignments(sid1, [
            {"shift_id": "s1", "person": "Alice", "points": 1.5},
            {"shift_id": "s2", "person": "Bob", "points": 1.5},
        ], saved_by="a@b.gov")

        # Overwrite with different case and only shift s1
        sid2, dropped = store.upsert_schedule(
            "fall 2026", cid, _sample_shifts(n=1), source="generated"
        )
        assert sid2 == sid1
        assert dropped == 1  # Bob's s2 assignment dropped
        shifts = store.get_schedule_shifts(sid1)
        assert [s["shift_id"] for s in shifts] == ["s1"]
        assert store.get_assignments(sid1)["s1"]["person"] == "Alice"
        assert "s2" not in store.get_assignments(sid1)

        # A new name creates a new schedule
        sid3, _ = store.upsert_schedule("Fall 2026 v2", cid, _sample_shifts(), source="upload")
        assert sid3 != sid1
        assert len(store.list_schedules()) == 2


def test_upsert_schedule_rejects_bad_input(app):
    with app.app_context():
        with pytest.raises(ValueError, match="no shifts"):
            store.upsert_schedule("Empty", None, [], source="upload")
        dup = _sample_shifts(n=1) * 2
        with pytest.raises(ValueError, match="duplicate"):
            store.upsert_schedule("Dup", None, dup, source="upload")


def test_schedule_to_shift_objects_applies_default_points(app):
    with app.app_context():
        rows = _sample_shifts(n=1)
        rows[0]["points"] = ""  # NULL -> loader default
        rows.append({
            "shift_id": "night1", "date": "2026-05-04", "date_end": "",
            "start_time": "22:00", "end_time": "06:00", "points": "",
        })
        sid, _ = store.upsert_schedule("Points Test", None, rows, source="upload")
        config = {"shift_points": {"default": 1.0, "night": 2.0,
                                   "night_start": "20:00", "night_end": "08:00"}}
        shifts = {s.shift_id: s for s in store.schedule_to_shift_objects(sid, config)}
        assert shifts["s1"].points == 1.0
        assert shifts["night1"].points == 2.0


def test_settings_and_default_schedule(app):
    with app.app_context():
        assert store.get_setting("nope", "fallback") == "fallback"
        store.set_setting("k", "v1")
        store.set_setting("k", "v2")
        assert store.get_setting("k") == "v2"

        assert store.get_default_schedule_id() is None
        sid, _ = store.upsert_schedule("Default Sched", None, _sample_shifts(), source="upload")
        store.set_default_schedule_id(sid)
        assert store.get_default_schedule_id() == sid
        store.set_default_schedule_id(None)
        assert store.get_default_schedule_id() is None


def test_contacts_upsert_and_fallback(app):
    with app.app_context():
        store.upsert_contact("Alice Smith", email="alice@fnal.gov", institution="Fermilab")
        contact = store.get_contact("alice smith")  # NOCASE
        assert contact["email"] == "alice@fnal.gov"
        assert contact["source"] == "contacts"

        # Blank email in a later upsert must not erase the stored one
        store.upsert_contact("Alice Smith", phone="+1 630 555 0000")
        contact = store.get_contact("Alice Smith")
        assert contact["email"] == "alice@fnal.gov"
        assert contact["phone"] == "+1 630 555 0000"

        # Fallback to the auth users table (seeded admin)
        from tests.conftest import ADMIN_EMAIL
        import sqlite3
        with sqlite3.connect(app.config["AUTH_DB_PATH"]) as conn:
            conn.execute("UPDATE users SET name = 'Seed Admin' WHERE email = ?", (ADMIN_EMAIL,))
        contact = store.get_contact("Seed Admin")
        assert contact["email"] == ADMIN_EMAIL
        assert contact["source"] == "users"

        assert store.get_contact("Nobody Here") is None
        assert store.get_contact("UNASSIGNED") is None


def test_bulk_upsert_contacts_skips_rows_without_contact_info(app):
    with app.app_context():
        store.bulk_upsert_contacts([
            {"person": "Has Email", "email": "x@y.gov", "institution": "ANL"},
            {"person": "No Info", "institution": "FNAL"},
        ])
        assert store.get_contact("Has Email")["email"] == "x@y.gov"
        assert store.get_contact("No Info") is None
