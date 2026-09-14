from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from school_secretary.config import get_settings
from school_secretary.db.models import Assignment, SubTask

STEP_TEMPLATES: dict[str, list[str]] = {
    "coding_lab": [
        "Read the lab PDF and rubric; list required operations and constraints",
        "Work 2–3 examples by hand and sketch the data structure / API",
        "Implement the core operations yourself (no generated solutions)",
        "Fill in the unit-test shells and run them",
        "Style, comments, and short write-up as required",
        "Submit with a 24-hour buffer",
    ],
    "essay": [
        "Read the prompt and rubric; note required word count and sources",
        "Draft a thesis and section outline only (no full prose yet)",
        "Gather readings and quote bank with citations",
        "Write your own draft from the outline",
        "Revise against the rubric (argument, sources, style)",
        "Proofread and submit with a 24-hour buffer",
    ],
    "other": [
        "Read the assignment instructions and any attachments",
        "Break the deliverable into the required parts",
        "Produce a first version of each part yourself",
        "Check against the rubric or grading notes",
        "Submit with a 24-hour buffer",
    ],
}


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(_tz())
    return dt.astimezone(_tz())


def plan_assignment(session: Session, assignment: Assignment, now: datetime | None = None) -> list[SubTask]:
    now = now or datetime.now(tz=_tz())
    due = _as_local(assignment.due_at) or now + timedelta(days=14)
    steps = STEP_TEMPLATES.get(assignment.assignment_type, STEP_TEMPLATES["other"])
    span = max((due - now).total_seconds(), 3600)
    session.query(SubTask).filter_by(assignment_id=assignment.id).delete()
    created: list[SubTask] = []
    for index, title in enumerate(steps):
        fraction = (index + 1) / (len(steps) + 0.5)
        micro_due = now + timedelta(seconds=span * fraction)
        if micro_due > due - timedelta(hours=4) and index < len(steps) - 1:
            micro_due = due - timedelta(hours=12 * (len(steps) - index))
        row = SubTask(
            assignment_id=assignment.id,
            title=title,
            due_at=micro_due,
            status="pending",
            sort_order=index,
        )
        session.add(row)
        created.append(row)
    assignment.planned = True
    session.flush()
    return created


def plan_unplanned(session: Session, now: datetime | None = None) -> list[Assignment]:
    rows = session.query(Assignment).filter_by(planned=False).all()
    for assignment in rows:
        plan_assignment(session, assignment, now=now)
    return rows


def format_plan(assignment: Assignment) -> str:
    tz = _tz()
    lines = [
        f"Plan for {assignment.course.code} — {assignment.title}",
        f"Type: {assignment.assignment_type} | due { _as_local(assignment.due_at) }",
        "Academic integrity: this is a work plan only. You write the actual work.",
        "",
    ]
    for task in assignment.subtasks:
        due = _as_local(task.due_at)
        due_s = due.astimezone(tz).strftime("%a %b %d %H:%M") if due else "unscheduled"
        lines.append(f"  {task.sort_order + 1}. [{due_s}] {task.title}")
    return "\n".join(lines)
