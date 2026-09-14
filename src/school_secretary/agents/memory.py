from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_secretary.config import get_settings
from school_secretary.db.models import Assignment, Course, StudyHabitEvent


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def record_study_session(
    session: Session,
    minutes: int,
    notes: str = "",
    course: Course | None = None,
    when: datetime | None = None,
    kind: str = "study_session",
) -> StudyHabitEvent:
    event = StudyHabitEvent(
        course_id=course.id if course else None,
        kind=kind,
        minutes=max(int(minutes), 0),
        notes=notes,
        occurred_at=when or datetime.now(tz=_tz()),
    )
    session.add(event)
    session.flush()
    return event


def weekly_minutes_by_course(session: Session, now: datetime | None = None) -> list[tuple[Course | None, int]]:
    now = now or datetime.now(tz=_tz())
    start = now - timedelta(days=7)
    rows = session.execute(
        select(StudyHabitEvent.course_id, func.coalesce(func.sum(StudyHabitEvent.minutes), 0))
        .where(StudyHabitEvent.occurred_at >= start)
        .where(StudyHabitEvent.kind == "study_session")
        .group_by(StudyHabitEvent.course_id)
    ).all()
    courses = {course.id: course for course in session.query(Course).all()}
    return [(courses.get(course_id), int(minutes)) for course_id, minutes in rows]


def suggest_focus(session: Session, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=_tz())
    upcoming = (
        session.query(Assignment)
        .filter(Assignment.due_at.is_not(None))
        .order_by(Assignment.due_at.asc())
        .all()
    )
    minutes = {course.id if course else None: total for course, total in weekly_minutes_by_course(session, now)}
    if not upcoming:
        return "No upcoming assignments in the database. Log a study session after you pick a course."
    future: list[tuple[datetime, int, Assignment]] = []
    for assignment in upcoming:
        due = assignment.due_at
        if due.tzinfo is None:
            due_cmp = due.replace(tzinfo=now.tzinfo)
        else:
            due_cmp = due
        if due_cmp < now:
            continue
        studied = minutes.get(assignment.course_id, 0)
        future.append((due_cmp, studied, assignment))
    if not future:
        return "Nothing upcoming — catch up on reading or log what you studied this week."
    future.sort(key=lambda row: (row[0], row[1]))
    top = future[0][2]
    studied = minutes.get(top.course_id, 0)
    return (
        f"Focus {top.course.code} — {top.title} "
        f"(due {top.due_at}, {studied} min logged in the last 7 days)."
    )


def format_habit_summary(session: Session, now: datetime | None = None) -> str:
    rows = weekly_minutes_by_course(session, now)
    if not rows:
        return "No study sessions logged this week. Use `school-secretary study --course 'CISC 235' --minutes 45`."
    parts = []
    for course, minutes in sorted(rows, key=lambda row: row[1], reverse=True):
        label = course.code if course else "unspecified"
        parts.append(f"{label}: {minutes} min")
    return "This week: " + "; ".join(parts)
