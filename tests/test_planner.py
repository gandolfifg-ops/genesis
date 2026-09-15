from datetime import datetime
from zoneinfo import ZoneInfo

from school_secretary.agents.planner import plan_assignment, steps_from_text
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
        titles = " ".join(task.title for task in tasks).lower()
        assert tasks
        assert "read the assignment instructions" not in titles
        assert "implement" in titles or "bst" in titles
        assert assignment.planned is True
        assert tasks[0].due_at < tasks[-1].due_at
        assert tasks[-1].due_at <= due
        tasks[1].status = "done"
        again = plan_assignment(session, assignment, now=now)
        assert again[1].status == "done"
        assert len(again) == len(tasks)


def test_steps_from_assignment_file_text_are_specific():
    corpus = (
        "Implement a binary search tree supporting insert, search, and delete.\n"
        "Part A: problem statement for the empty tree.\n"
        "Correctness of insert, search, and delete: 40%.\n"
        "Unit tests covering empty tree: 20%.\n"
    )
    steps = steps_from_text("Lab 2 — Binary Search Trees", corpus, "coding_lab")
    blob = " ".join(steps).lower()
    assert "read the assignment instructions" not in blob
    assert any("insert" in step.lower() or "bst" in step.lower() or "binary" in step.lower() for step in steps)
    assert any("rubric" in step.lower() or "problem statement" in step.lower() for step in steps)


def test_cad_and_rubric_steps_from_file_text():
    corpus = (
        "MECH 221 Lab 3 — Orthographic views\n"
        "Section 1. Problem statement for the bracket.\n"
        "Complete the SolidWorks CAD drawing for part A.\n"
        "Accuracy: 40%. Presentation: 20%. Dimensioning: 20%. Title block: 20%.\n"
        "Review the attached rubric before submitting.\n"
    )
    steps = steps_from_text("Lab 3 — Orthographic CAD", corpus, "other")
    blob = " ".join(steps).lower()
    assert "read the assignment instructions" not in blob
    assert any("problem statement" in step.lower() for step in steps)
    assert any("cad" in step.lower() for step in steps)
    assert any("rubric" in step.lower() for step in steps)
