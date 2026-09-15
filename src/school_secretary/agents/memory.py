from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_secretary.config import get_settings
from school_secretary.db.live import live_course_ids
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


def course_minutes(
    session: Session,
    course_id: int | None,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(tz=_tz())
    start = now - timedelta(days=days)
    query = (
        select(func.coalesce(func.sum(StudyHabitEvent.minutes), 0))
        .where(StudyHabitEvent.occurred_at >= start)
        .where(StudyHabitEvent.kind == "study_session")
    )
    if course_id is None:
        query = query.where(StudyHabitEvent.course_id.is_(None))
    else:
        query = query.where(StudyHabitEvent.course_id == course_id)
    return int(session.execute(query).scalar_one() or 0)


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


def last_study_event(session: Session, course_id: int | None = None) -> StudyHabitEvent | None:
    query = session.query(StudyHabitEvent).filter_by(kind="study_session")
    if course_id is not None:
        query = query.filter_by(course_id=course_id)
    return query.order_by(StudyHabitEvent.occurred_at.desc()).first()


def study_streak_days(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(tz=_tz())
    dates = set()
    for (occurred,) in session.query(StudyHabitEvent.occurred_at).filter_by(kind="study_session"):
        local = occurred.astimezone(_tz()) if occurred.tzinfo else occurred.replace(tzinfo=_tz())
        dates.add(local.date())
    streak = 0
    cursor = now.date()
    while cursor in dates:
        streak += 1
        cursor = cursor - timedelta(days=1)
    if streak == 0:
        cursor = now.date() - timedelta(days=1)
        while cursor in dates:
            streak += 1
            cursor = cursor - timedelta(days=1)
    return streak


def suggest_focus(session: Session, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=_tz())
    upcoming = (
        session.query(Assignment)
        .filter(Assignment.due_at.is_not(None))
        .order_by(Assignment.due_at.asc())
        .all()
    )
    live_ids = live_course_ids(session)
    if live_ids:
        upcoming = [row for row in upcoming if row.course_id in live_ids]
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


def suggested_block(session: Session, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=_tz())
    focus = suggest_focus(session, now)
    if "No upcoming" in focus or "Nothing upcoming" in focus:
        return "Suggested block: 25 min reading, then log it with /study."
    # Pull minutes out of the focus line if present.
    neglected = ", 0 min logged" in focus
    length = 90 if neglected else 45
    last = last_study_event(session)
    last_bit = ""
    if last and last.occurred_at:
        when = last.occurred_at
        last_bit = f" Last session: {when.strftime('%a %H:%M')} ({last.minutes} min)."
    streak = study_streak_days(session, now)
    return f"Suggested block: {length} min. Streak: {streak} day(s).{last_bit}"


def format_habit_summary(session: Session, now: datetime | None = None) -> str:
    now = now or datetime.now(tz=_tz())
    rows = weekly_minutes_by_course(session, now)
    streak = study_streak_days(session, now)
    if not rows:
        example = "CISC 235"
        live_ids = live_course_ids(session)
        if live_ids:
            live = session.get(Course, live_ids[0])
            if live:
                example = live.code
        return (
            "No study sessions logged this week. "
            f"Use `school-secretary study --course '{example}' --minutes 45` "
            f"(streak: {streak} day(s))."
        )
    parts = []
    for course, minutes in sorted(rows, key=lambda row: row[1], reverse=True):
        label = course.code if course else "unspecified"
        parts.append(f"{label}: {minutes} min")
    last = last_study_event(session)
    last_bit = ""
    if last and last.occurred_at:
        last_bit = f" Last log: {last.occurred_at.strftime('%a %d %b %H:%M')}."
    return f"This week: {'; '.join(parts)}. Streak: {streak} day(s).{last_bit}"


def habit_note_for_course(session: Session, course_id: int, now: datetime | None = None) -> str:
    minutes = course_minutes(session, course_id, days=7, now=now)
    if minutes <= 0:
        return "No minutes logged on this course this week — front-loaded the early steps."
    if minutes >= 90:
        return f"{minutes} min logged this week — extra buffer on later steps."
    return f"{minutes} min logged on this course this week."
