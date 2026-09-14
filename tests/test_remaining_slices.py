from school_secretary.agents.memory import record_study_session
from school_secretary.agents.planner import plan_assignment
from school_secretary.calendar_sync import sync_calendar
from school_secretary.db.models import Assignment, Course
from school_secretary.db.session import init_db, session_scope
from school_secretary.whatsapp_app.server import reply_to_text


def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    monkeypatch.setenv("WHATSAPP_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    return get_settings()


def test_calendar_writes_ics_without_google(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = _iso(tmp_path, monkeypatch)
    init_db(settings)
    tz = ZoneInfo("America/Toronto")
    with session_scope(settings) as session:
        course = Course(org_unit_id="9", code="CISC 235", name="DS", term="F26")
        session.add(course)
        session.flush()
        session.add(
            Assignment(
                course_id=course.id,
                d2l_id="lab",
                title="Lab 2",
                due_at=datetime(2026, 10, 3, 23, 59, tzinfo=tz),
                assignment_type="coding_lab",
            )
        )
    result = sync_calendar(settings)
    assert result["transport"] == "ics"
    path = tmp_path / "calendar" / "school-secretary.ics"
    text = path.read_text(encoding="utf-8")
    assert "BEGIN:VEVENT" in text
    assert "Lab 2" in text


def test_whatsapp_mock_briefing_without_token(tmp_path, monkeypatch):
    from school_secretary.ingest.fixtures import ingest_fixtures

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    reply = reply_to_text("/briefing", settings, peer="mock")
    assert "Morning briefing" in reply or "briefing" in reply.lower()
    outbox = settings.whatsapp_outbox_path.read_text(encoding="utf-8")
    assert "mock" in outbox


def test_planner_uses_logged_minutes(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = _iso(tmp_path, monkeypatch)
    init_db(settings)
    tz = ZoneInfo("America/Toronto")
    now = datetime(2026, 9, 14, 12, 0, tzinfo=tz)
    due = datetime(2026, 10, 3, 23, 59, tzinfo=tz)
    with session_scope(settings) as session:
        course = Course(org_unit_id="1", code="CISC 235", name="DS", term="F26")
        session.add(course)
        session.flush()
        assignment = Assignment(
            course_id=course.id,
            d2l_id="x",
            title="Lab 2",
            due_at=due,
            assignment_type="coding_lab",
        )
        session.add(assignment)
        session.flush()
        none = plan_assignment(session, assignment, now=now)
        record_study_session(session, minutes=120, notes="logged", course=course, when=now)
        lots = plan_assignment(session, assignment, now=now)
        assert none[0].due_at < lots[0].due_at
