from datetime import datetime
from zoneinfo import ZoneInfo

from school_secretary.agents.planner import STEP_TEMPLATES, plan_assignment
from school_secretary.db.models import Assignment, Course
from school_secretary.db.session import init_db, session_scope


def test_coding_lab_gets_micro_deadlines(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    settings = get_settings()
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
            instructions="implement bst",
        )
        session.add(assignment)
        session.flush()
        tasks = plan_assignment(session, assignment, now=now)
        assert len(tasks) == len(STEP_TEMPLATES["coding_lab"])
        assert assignment.planned is True
        assert tasks[0].due_at < tasks[-1].due_at
        assert tasks[-1].due_at <= due
