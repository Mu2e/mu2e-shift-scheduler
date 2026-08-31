"""Tests for the calendar views: tabs, defaults, multi-day expansion, Empty."""
from app import store

BLOCK_ROWS = [
    {
        "shift_id": "day-w1", "shift_type": "Day", "days": "Mon,Tue,Wed,Thu",
        "date": "2026-05-04", "date_end": "2026-05-07",
        "start_time": "08:00", "end_time": "16:00", "points": "1.0",
    },
    {
        "shift_id": "night-w1", "shift_type": "Night", "days": "",
        "date": "2026-05-04", "date_end": "",
        "start_time": "20:00", "end_time": "04:00", "points": "2.0",
    },
]


def _make_schedule(app, name="Fall 2026", classification="Run Coordinators"):
    with app.app_context():
        classifications = {c["name"]: c for c in store.list_classifications()}
        cid = classifications[classification]["id"]
        sid, _ = store.upsert_schedule(name, cid, BLOCK_ROWS, source="upload")
        return sid


def test_calendar_tabs_show_visible_classifications(admin_client, app):
    _make_schedule(app)
    response = admin_client.get("/calendar")
    html = response.get_data(as_text=True)
    assert "General Shifts" in html
    assert "Run Coordinators" in html
    assert "Oncall DAQ Experts" in html

    # Hiding a classification removes its tab
    with app.app_context():
        general = next(c for c in store.list_classifications() if c["name"] == "General Shifts")
        store.update_classification(general["id"], show_tab=False)
    html = admin_client.get("/calendar").get_data(as_text=True)
    assert "General Shifts" not in html


def test_multi_day_shift_appears_on_every_covered_day(admin_client, app):
    sid = _make_schedule(app)
    with app.app_context():
        store.save_assignments(sid, [
            {"shift_id": "day-w1", "person": "Bob", "institution": "Argonne"},
        ], saved_by="a@b.gov")
    html = admin_client.get(
        "/calendar?schedule=Fall+2026&view=month&date=2026-05-01"
    ).get_data(as_text=True)
    # Bob appears once per covered day (Mon-Thu), and the empty Night shift
    # renders as "Empty" rather than UNASSIGNED.
    assert html.count("Day:") == 4
    assert html.count(">Bob</a>") == 4
    assert "day 2 of 4" in html
    assert "Empty" in html
    assert "UNASSIGNED" not in html


def test_week_and_today_views(admin_client, app):
    sid = _make_schedule(app)
    with app.app_context():
        store.save_assignments(sid, [
            {"shift_id": "day-w1", "person": "Bob", "email": "bob@fnal.gov"},
        ], saved_by="a@b.gov")

    week_html = admin_client.get(
        "/calendar?schedule=Fall+2026&view=week&date=2026-05-05"
    ).get_data(as_text=True)
    assert "view-week" in week_html
    assert week_html.count(">Bob</a>") == 4  # Mon-Thu within this week

    today_html = admin_client.get(
        "/calendar?schedule=Fall+2026&view=today&date=2026-05-05"
    ).get_data(as_text=True)
    assert "Tuesday, May 5, 2026" in today_html
    assert "Day:" in today_html
    assert "day 2 of 4" in today_html
    assert 'mailto:bob@fnal.gov' in today_html


def test_default_schedule_setting_selects_calendar(admin_client, app):
    _make_schedule(app, name="Older", classification="General Shifts")
    sid = _make_schedule(app, name="Preferred Default", classification="Run Coordinators")
    with app.app_context():
        store.set_default_schedule_id(sid)
    html = admin_client.get("/calendar").get_data(as_text=True)
    assert "Preferred Default" in html


def test_unknown_schedule_name_flashes_warning(admin_client, app):
    _make_schedule(app)
    html = admin_client.get("/calendar?schedule=NoSuch").get_data(as_text=True)
    assert "No schedule named" in html


def test_legacy_file_source_still_works(admin_client, app):
    from pathlib import Path
    csv_text = (
        "shift_id,date,date_end,start_time,end_time,shift_type,days\n"
        "b1,2026-05-04,2026-05-06,08:00,16:00,Day,\"Mon,Tue,Wed\"\n"
    )
    (Path(app.config["CSV_DIR"]) / "legacy.csv").write_text(csv_text, encoding="utf-8")
    html = admin_client.get(
        "/calendar?source=schedule&schedule=legacy.csv&date=2026-05-01"
    ).get_data(as_text=True)
    # Legacy CSVs get multi-day expansion too
    assert html.count("Day:") == 3
    assert html.count("Empty") >= 3
