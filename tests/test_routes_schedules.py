"""Tests for named-schedule routes, shift setup generation, and solve sources."""
import io
from pathlib import Path

from app import store

BLOCK_CSV = (
    "shift_id,schedule_name,week,block_number,block_name,block_type,days,date,date_end,start_time,end_time,shift_type,points\n"
    "t-W01-B1-S1-day,Test,1,1,Day,day,\"Mon,Tue\",2026-05-04,2026-05-05,08:00,16:00,Day,1.0\n"
    "t-W01-B2-S1-night,Test,1,2,Night,night,,2026-05-04,,20:00,04:00,Night,2.0\n"
)

PEOPLE_CSV = (
    "name,institution,email,phone,pref_1\n"
    "Alice,Fermilab,alice@fnal.gov,+1 630 555 1111,t-W01-B1-S1-day\n"
    "Bob,Argonne,bob@anl.gov,,t-W01-B2-S1-night\n"
)


def _save_schedule(admin_client, name="Fall 2026", classification="run-coordinators",
                   overwrite=False):
    data = {
        "name": name,
        "classification": classification,
        "csv_file": (io.BytesIO(BLOCK_CSV.encode()), "test.csv"),
    }
    if overwrite:
        data["overwrite"] = "1"
    return admin_client.post(
        "/schedules/save", data=data,
        content_type="multipart/form-data", follow_redirects=False,
    )


def test_save_schedule_from_upload(admin_client, app):
    response = _save_schedule(admin_client)
    assert response.status_code == 302
    with app.app_context():
        schedule = store.get_schedule_by_name("Fall 2026")
        assert schedule is not None
        assert schedule["classification_slug"] == "run-coordinators"
        shifts = store.get_schedule_shifts(schedule["id"])
        assert {s["shift_id"] for s in shifts} == {"t-W01-B1-S1-day", "t-W01-B2-S1-night"}
        assert {s["shift_name"] for s in shifts} == {"Day", "Night"}
    # Backing CSV written for legacy flows
    assert (Path(app.config["CSV_DIR"]) / "fall-2026.csv").exists()


def test_save_duplicate_name_shows_confirm_page(admin_client, app):
    _save_schedule(admin_client)
    response = _save_schedule(admin_client)  # same name again, no overwrite flag
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Overwrite existing schedule" in html
    assert 'name="overwrite" value="1"' in html

    # Confirming via the staged re-post overwrites
    response = admin_client.post(
        "/schedules/save",
        data={"name": "fall 2026", "classification": "general-shifts",
              "from_staged": "1", "overwrite": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert len(store.list_schedules()) == 1
        assert store.get_schedule_by_name("Fall 2026")["classification_slug"] == "general-shifts"


def test_schedule_export_round_trip(admin_client, app):
    _save_schedule(admin_client)
    with app.app_context():
        sid = store.get_schedule_by_name("Fall 2026")["id"]
    response = admin_client.get(f"/schedules/{sid}/export.csv")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "t-W01-B1-S1-day" in text
    # Exported CSV loads back through the scheduler loader
    from scheduler.loader import load_shifts
    path = Path(app.config["CSV_DIR"]) / "roundtrip.csv"
    path.write_text(text, encoding="utf-8")
    shifts = load_shifts(str(path), {})
    assert len(shifts) == 2


def test_delete_schedule(admin_client, app):
    _save_schedule(admin_client)
    with app.app_context():
        sid = store.get_schedule_by_name("Fall 2026")["id"]
    response = admin_client.post(f"/schedules/{sid}/delete", follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        assert store.get_schedule_by_name("Fall 2026") is None


def test_shift_setup_generate_creates_schedule(admin_client, app):
    response = admin_client.post(
        "/admin/shift-setup/generate",
        data={
            "name": "Generated 2026",
            "classification": "general-shifts",
            "date_start": "2026-05-04",
            "date_end": "2026-05-17",
            "block_name_0": "Weekdays",
            "block_days_0[]": ["0", "1", "2", "3"],
            "block_name_1": "Weekends",
            "block_days_1[]": ["4", "5", "6"],
            "shift_name[]": ["Day", "Night"],
            "shift_start[]": ["08:00", "20:00"],
            "shift_end[]": ["20:00", "08:00"],
            "shift_weight[]": ["1.0", "2.0"],
            "repeat": "week",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    with app.app_context():
        schedule = store.get_schedule_by_name("Generated 2026")
        assert schedule is not None
        shifts = store.get_schedule_shifts(schedule["id"])
        # 2 blocks x 2 shifts x 2 full weeks
        assert len(shifts) == 8
        assert {s["block_name"] for s in shifts} == {"Weekdays", "Weekends"}
        weekday_day = next(
            s for s in shifts
            if s["block_name"] == "Weekdays" and s["shift_name"] == "Day" and s["week"] == 1
        )
        assert weekday_day["date"] == "2026-05-04"
        assert weekday_day["date_end"] == "2026-05-07"
        assert weekday_day["days"] == "Mon,Tue,Wed,Thu"
        assert weekday_day["points"] == 1.0
        weekend_night = next(
            s for s in shifts
            if s["block_name"] == "Weekends" and s["shift_name"] == "Night" and s["week"] == 1
        )
        assert weekend_night["date"] == "2026-05-08"
        assert weekend_night["date_end"] == "2026-05-10"
        assert weekend_night["points"] == 2.0
    # Backing CSV loads through the scheduler loader
    from scheduler.loader import load_shifts
    backing = Path(app.config["CSV_DIR"]) / "generated-2026.csv"
    assert backing.exists()
    assert len(load_shifts(str(backing), {})) == 8


def test_shift_setup_consecutive_block_without_weekdays(admin_client, app):
    response = admin_client.post(
        "/admin/shift-setup/generate",
        data={
            "name": "Oncall 2026",
            "date_start": "2026-05-04",
            "date_end": "2026-05-31",
            "block_name_0": "Oncall Week",
            "block_length_0": "7",
            "shift_name[]": ["Oncall"],
            "shift_start[]": ["00:00"],
            "shift_end[]": ["23:59"],
            "shift_weight[]": ["3.5"],
            "repeat": "2week",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    with app.app_context():
        shifts = store.get_schedule_shifts(store.get_schedule_by_name("Oncall 2026")["id"])
        assert [s["date"] for s in shifts] == ["2026-05-04", "2026-05-18"]
        assert shifts[0]["date_end"] == "2026-05-10"
        assert shifts[0]["points"] == 3.5


def test_shift_setup_invalid_input_rerenders_form(admin_client):
    response = admin_client.post(
        "/admin/shift-setup/generate",
        data={
            "name": "Bad", "date_start": "2026-05-10", "date_end": "2026-05-04",
            "block_name_0": "Weekdays", "block_days_0[]": ["0", "1"],
            "shift_name[]": ["Day"], "shift_start[]": ["08:00"], "shift_end[]": ["16:00"],
            "shift_weight[]": [""], "repeat": "week",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Stop date must be on or after the start date" in response.get_data(as_text=True)

    # Missing blocks entirely is caught with a usable message
    response = admin_client.post(
        "/admin/shift-setup/generate",
        data={
            "name": "Bad", "date_start": "2026-05-04", "date_end": "2026-05-10",
            "shift_name[]": ["Day"], "shift_start[]": ["08:00"], "shift_end[]": ["16:00"],
            "shift_weight[]": [""], "repeat": "week",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "at least one block" in response.get_data(as_text=True)


def test_shift_setup_requires_admin(user_client):
    assert user_client.get("/admin/shift-setup", follow_redirects=False).status_code == 302


def test_solve_from_stored_schedule_and_save_back(admin_client, app):
    _save_schedule(admin_client)
    with app.app_context():
        sid = store.get_schedule_by_name("Fall 2026")["id"]

    response = admin_client.post(
        "/solve",
        data={
            "schedule_id": str(sid),
            "people_file": (io.BytesIO(PEOPLE_CSV.encode()), "people.csv"),
            "target": "1.5", "min": "1.0", "max": "2.0",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/results" in response.headers["Location"]

    html = admin_client.get("/results").get_data(as_text=True)
    assert "Fall 2026" in html  # save-to-schedule checkbox references the source

    response = admin_client.post(
        "/results/save",
        data={"filename": "solved.json", "save_to_schedule": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assignments = store.get_assignments(sid)
        assert set(assignments) == {"t-W01-B1-S1-day", "t-W01-B2-S1-night"}
        people = {a["person"] for a in assignments.values()}
        assert people == {"Alice", "Bob"}
        # Contact info from the people CSV landed in the contacts table
        assert store.get_contact("Alice")["email"] == "alice@fnal.gov"
    assert (Path(app.config["DATA_DIR"]) / "solved.json").exists()

    # The calendar now shows the assigned people with contact links
    calendar_html = admin_client.get(
        "/calendar?schedule=Fall+2026&view=month&date=2026-05-01"
    ).get_data(as_text=True)
    assert "Alice" in calendar_html
    assert "mailto:alice@fnal.gov" in calendar_html


def test_solve_from_server_people_file(admin_client, app):
    _save_schedule(admin_client)
    (Path(app.config["CSV_DIR"]) / "people.csv").write_text(PEOPLE_CSV, encoding="utf-8")
    with app.app_context():
        sid = store.get_schedule_by_name("Fall 2026")["id"]
    response = admin_client.post(
        "/solve",
        data={
            "schedule_id": str(sid),
            "people_file_server": "people.csv",
            "target": "1.5", "min": "1.0", "max": "2.0",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/results" in response.headers["Location"]
