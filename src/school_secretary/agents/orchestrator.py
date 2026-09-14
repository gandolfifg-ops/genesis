from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from school_secretary.agents.drafting import describe_scaffold, scaffold_assignment
from school_secretary.agents.memory import (
    format_habit_summary,
    record_study_session,
    suggest_focus,
    suggested_block,
)
from school_secretary.agents.planner import format_plan, plan_unplanned
from school_secretary.agents.triage import triage_pending
from school_secretary.config import Settings, get_settings
from school_secretary.db.live import live_course_ids
from school_secretary.db.models import Announcement, Assignment, Course, SubTask
from school_secretary.db.session import session_scope
from school_secretary.rag.index import index_documents
from school_secretary.rag.query import answer_question


def run_agents(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    with session_scope(settings) as session:
        triaged = triage_pending(session)
        planned = plan_unplanned(session)
    indexed = index_documents(settings)
    from school_secretary.calendar_sync import sync_calendar

    calendar = sync_calendar(settings)
    return {
        "triaged": len(triaged),
        "planned": len(planned),
        "indexed": indexed,
        "calendar_events": int(calendar["events"]),
    }


def find_course(session: Session, query: str) -> Course | None:
    q = query.strip()
    course = session.query(Course).filter(Course.code.ilike(q)).one_or_none()
    if course:
        return course
    return session.query(Course).filter(Course.code.ilike(f"%{q}%")).one_or_none() or session.query(
        Course
    ).filter(Course.name.ilike(f"%{q}%")).one_or_none()


def find_assignment(session: Session, query: str) -> Assignment | None:
    q = query.strip()
    live_ids = live_course_ids(session)
    if q.isdigit():
        row = session.get(Assignment, int(q))
        return row
    matches = session.query(Assignment).filter(Assignment.title.ilike(f"%{q}%")).all()
    if live_ids:
        live_matches = [row for row in matches if row.course_id in live_ids]
        if live_matches:
            return live_matches[0]
    return matches[0] if matches else None


def professor_email_draft(
    session: Session,
    topic: str,
    course_query: str | None = None,
) -> str:
    course = find_course(session, course_query) if course_query else None
    upcoming = session.query(Assignment).order_by(Assignment.due_at.asc()).all()
    if course:
        upcoming = [row for row in upcoming if row.course_id == course.id]
    soon = [row for row in upcoming if row.due_at][:3]
    schedule = "; ".join(
        f"{row.title} due {row.due_at.strftime('%b %d')}" for row in soon
    ) or "no upcoming dropbox items on file"
    course_label = course.code if course else "the course"
    return (
        f"Subject: {topic} — {course_label}\n\n"
        f"Dear Professor,\n\n"
        f"I hope you are well. I am writing about {topic} in {course_label}. "
        f"For context, my current deadlines are: {schedule}. "
        f"Would you have a few minutes during office hours, or could you let me know a better time?\n\n"
        f"Thank you very much for your time.\n\n"
        f"Kind regards,\n"
        f"Francesco\n"
    )


def render_briefing(kind: str, settings: Settings | None = None, now: datetime | None = None) -> str:
    settings = settings or get_settings()
    tz = ZoneInfo(settings.timezone)
    now = now or datetime.now(tz=tz)
    window = timedelta(hours=36) if kind == "morning" else timedelta(hours=18)
    with session_scope(settings) as session:
        live_ids = live_course_ids(session)
        announcements = session.query(Announcement).order_by(Announcement.posted_at.desc())
        assignments = session.query(Assignment).filter(Assignment.due_at.is_not(None)).order_by(
            Assignment.due_at.asc()
        )
        if live_ids:
            announcements = announcements.filter(Announcement.course_id.in_(live_ids))
            assignments = assignments.filter(Assignment.course_id.in_(live_ids))
        announcements = announcements.limit(8).all()
        assignments = assignments.all()
        due_soon = []
        for row in assignments:
            due = row.due_at
            if due.tzinfo is None:
                due_cmp = due.replace(tzinfo=tz)
            else:
                due_cmp = due
            if now <= due_cmp <= now + timedelta(days=21):
                due_soon.append(row)
        recent = []
        for ann in announcements:
            posted = ann.posted_at
            if posted is None:
                continue
            if posted.tzinfo is None:
                posted_cmp = posted.replace(tzinfo=tz)
            else:
                posted_cmp = posted
            if now - posted_cmp <= window + timedelta(days=2):
                recent.append(ann)
        title = "Morning briefing" if kind == "morning" else "Evening wrap-up"
        lines = [
            f"{title} — {now.strftime('%A %d %b %Y %H:%M %Z')}",
            "",
            format_habit_summary(session, now),
            suggest_focus(session, now),
            suggested_block(session, now),
            "",
            "Announcements:",
        ]
        if recent:
            for ann in recent[:5]:
                code = ann.course.code
                lines.append(f"  - [{ann.category}] {code}: {ann.title}")
        else:
            lines.append("  (none in the recent window)")
        lines.append("")
        lines.append("Coming due:")
        if due_soon:
            for row in due_soon[:6]:
                due = row.due_at
                due_s = due.strftime("%a %b %d %H:%M")
                lines.append(f"  - {row.course.code}: {row.title} ({due_s})")
        else:
            lines.append("  (nothing in the next 21 days)")
        pending_q = (
            session.query(SubTask)
            .filter(SubTask.status == "pending")
            .filter(SubTask.due_at.is_not(None))
            .order_by(SubTask.due_at.asc())
        )
        if live_ids:
            pending_q = pending_q.join(Assignment).filter(Assignment.course_id.in_(live_ids))
        pending_steps = pending_q.limit(5).all()
        lines.append("")
        if kind == "evening":
            lines.append("Open micro-deadlines:")
        else:
            lines.append("Next planned steps:")
        if pending_steps:
            for task in pending_steps:
                due = task.due_at
                due_s = due.strftime("%a %b %d %H:%M") if due else "?"
                code = task.assignment.course.code if task.assignment else ""
                lines.append(f"  - {code}: {task.title} ({due_s})")
        else:
            lines.append("  (run /plan on an assignment to generate micro-deadlines)")
        ics = settings.calendar_dir / "school-secretary.ics"
        lines.append("")
        if ics.exists():
            lines.append(f"Calendar: {ics} — import into Google, or set GOOGLE_OAUTH_* and run calendar-sync.")
        else:
            lines.append("Calendar: run `uv run school-secretary calendar-sync` to write an ICS file.")
        record_study_session(
            session,
            minutes=0,
            notes=f"{kind} briefing",
            kind="briefing_read",
            when=now,
        )
        if kind == "evening":
            lines += ["", "Wrap-up: log what you actually studied so tomorrow's plan is honest. /study CISC 235 45"]
        else:
            lines += ["", "Ask a syllabus question with /ask, or /plan the next lab. Scaffolding only — you write the work."]
        return "\n".join(lines)


def ask(question: str, settings: Settings | None = None) -> str:
    return answer_question(question, settings=settings)


def plan_and_format(query: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with session_scope(settings) as session:
        assignment = find_assignment(session, query)
        if assignment is None:
            return f"No assignment matched {query!r}."
        from school_secretary.agents.planner import plan_assignment

        if not assignment.subtasks:
            plan_assignment(session, assignment)
            session.refresh(assignment)
        return format_plan(assignment)


def scaffold_and_describe(query: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    with session_scope(settings) as session:
        assignment = find_assignment(session, query)
        if assignment is None:
            return f"No assignment matched {query!r}."
        path = scaffold_assignment(assignment, settings)
        return describe_scaffold(path)
