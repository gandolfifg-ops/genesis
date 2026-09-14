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
        first_due = none[0].due_at
        record_study_session(session, minutes=120, notes="logged", course=course, when=now)
        lots = plan_assignment(session, assignment, now=now)
        assert first_due < lots[0].due_at


def test_fixture_ingest_is_idempotent(tmp_path, monkeypatch):
    from school_secretary.db.models import Announcement, Assignment, Course, Document, StudyHabitEvent
    from school_secretary.ingest.fixtures import ingest_fixtures

    settings = _iso(tmp_path, monkeypatch)
    first = ingest_fixtures(settings)
    second = ingest_fixtures(settings)
    assert first["courses"] == second["courses"] == 3
    assert first["assignments"] == second["assignments"] == 3
    assert first["announcements"] == second["announcements"] == 3
    assert first["habits"] == second["habits"]
    assert first["documents"] == second["documents"]
    with session_scope(settings) as session:
        assert session.query(Course).count() == 3
        assert session.query(Assignment).count() == 3
        assert session.query(Announcement).count() == 3
        assert session.query(Document).count() == first["documents"] == 7
        assert session.query(StudyHabitEvent).filter_by(kind="study_session").count() == 4


def test_calendar_sync_idempotent_uids(tmp_path, monkeypatch):
    from school_secretary.db.models import CalendarEvent
    from school_secretary.ingest.fixtures import ingest_fixtures
    from school_secretary.agents.orchestrator import run_agents

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    a = sync_calendar(settings)
    b = sync_calendar(settings)
    assert a["events"] == b["events"]
    with session_scope(settings) as session:
        rows = session.query(CalendarEvent).all()
        assert len(rows) == a["events"]
        uids = [row.uid for row in rows]
        assert len(uids) == len(set(uids))
        assert any(uid.startswith("ss-assignment-") for uid in uids)


def test_rag_distinguishes_course_late_policies(tmp_path, monkeypatch):
    from school_secretary.agents.orchestrator import ask, run_agents
    from school_secretary.ingest.fixtures import ingest_fixtures

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    cisc235 = ask("What is the late penalty for CISC 235?", settings=settings)
    cisc365 = ask("What is the late penalty for CISC 365?", settings=settings)
    assert "10%" in cisc235 or "10 %" in cisc235
    assert "5%" in cisc365 or "5 %" in cisc365


def test_mcp_lists_habit_and_subtask_tools(tmp_path, monkeypatch):
    import asyncio

    from school_secretary.agents.orchestrator import run_agents
    from school_secretary.ingest.fixtures import ingest_fixtures
    from school_secretary.mcp_app.server import build_mcp

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    mcp = build_mcp(settings)
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"list_courses", "query_syllabus", "list_habits", "list_subtasks", "sync_deadlines", "db_status"} <= names


def test_briefing_lists_next_steps_and_does_not_inflate_streak(tmp_path, monkeypatch):
    from school_secretary.agents.memory import study_streak_days
    from school_secretary.agents.orchestrator import render_briefing, run_agents
    from school_secretary.db.models import StudyHabitEvent
    from school_secretary.ingest.fixtures import DEMO_NOW, ingest_fixtures

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    with session_scope(settings) as session:
        before = study_streak_days(session, now=DEMO_NOW)
        sessions_before = session.query(StudyHabitEvent).filter_by(kind="study_session").count()
    morning = render_briefing("morning", settings=settings, now=DEMO_NOW)
    evening = render_briefing("evening", settings=settings, now=DEMO_NOW)
    assert "Next planned steps:" in morning
    assert "Calendar:" in morning
    assert "Open micro-deadlines:" in evening
    assert "/study" in evening
    with session_scope(settings) as session:
        after = study_streak_days(session, now=DEMO_NOW)
        sessions_after = session.query(StudyHabitEvent).filter_by(kind="study_session").count()
        reads = session.query(StudyHabitEvent).filter_by(kind="briefing_read").count()
    assert after == before
    assert sessions_after == sessions_before
    assert reads == 2


def test_planner_keeps_completed_subtasks(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from school_secretary.agents.planner import STEP_TEMPLATES, plan_assignment
    from school_secretary.db.models import SubTask

    settings = _iso(tmp_path, monkeypatch)
    init_db(settings)
    tz = ZoneInfo("America/Toronto")
    now = datetime(2026, 9, 14, 12, 0, tzinfo=tz)
    due = datetime(2026, 10, 3, 23, 59, tzinfo=tz)
    with session_scope(settings) as session:
        course = Course(org_unit_id="keep", code="CISC 235", name="DS", term="F26")
        session.add(course)
        session.flush()
        assignment = Assignment(
            course_id=course.id,
            d2l_id="keep",
            title="Lab 2",
            due_at=due,
            assignment_type="coding_lab",
        )
        session.add(assignment)
        session.flush()
        first = plan_assignment(session, assignment, now=now)
        first[0].status = "done"
        kept_due = first[0].due_at
        second = plan_assignment(session, assignment, now=now)
        assert len(second) == len(STEP_TEMPLATES["coding_lab"])
        assert second[0].status == "done"
        assert second[0].due_at == kept_due
        assert session.query(SubTask).filter_by(assignment_id=assignment.id).count() == len(second)


def test_help_and_habits_routes(tmp_path, monkeypatch):
    from school_secretary.ingest.fixtures import ingest_fixtures
    from school_secretary.telegram_app.handlers import route_locally

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    help_text = route_locally("/help", settings)
    assert "/habits" in help_text
    assert "/calendar" in help_text
    habits = route_locally("/habits", settings)
    assert "This week:" in habits or "No study sessions" in habits
    assert "Streak:" in habits


def test_suggested_block_does_not_treat_sixty_as_zero(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from school_secretary.agents.memory import record_study_session, suggested_block

    settings = _iso(tmp_path, monkeypatch)
    init_db(settings)
    tz = ZoneInfo("America/Toronto")
    now = datetime(2026, 9, 14, 12, 0, tzinfo=tz)
    with session_scope(settings) as session:
        course = Course(org_unit_id="365", code="CISC 365", name="Algo", term="F26")
        session.add(course)
        session.flush()
        session.add(
            Assignment(
                course_id=course.id,
                d2l_id="a1",
                title="Assignment 1",
                due_at=datetime(2026, 9, 28, 23, 59, tzinfo=tz),
                assignment_type="coding_lab",
            )
        )
        record_study_session(session, minutes=60, notes="dp", course=course, when=now)
        block = suggested_block(session, now)
    assert "45 min" in block
    assert "90 min" not in block

