from __future__ import annotations

import json

from school_secretary.agents.memory import record_study_session
from school_secretary.agents.orchestrator import (
    ask,
    find_assignment,
    find_course,
    plan_and_format,
    professor_email_draft,
    render_briefing,
    scaffold_and_describe,
)
from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Announcement, Assignment, Course, SubTask
from school_secretary.db.session import session_scope

MCP_PORT_DEFAULT = 43147


def build_mcp(settings: Settings | None = None):
    from mcp.server.mcpserver import MCPServer

    settings = settings or get_settings()
    mcp = MCPServer(
        name="school-secretary",
        instructions=(
            "Local Queen's onQ secretary. Query SQLite + ChromaDB. "
            "Never complete student assignments; scaffolding only."
        ),
    )

    @mcp.tool()
    def list_courses() -> str:
        """List courses stored in the local SQLite database."""
        with session_scope(settings) as session:
            rows = session.query(Course).order_by(Course.code).all()
            if not rows:
                return "No courses. Run `uv run school-secretary demo` first."
            return "\n".join(f"{c.org_unit_id} | {c.code} | {c.name}" for c in rows)

    @mcp.tool()
    def list_assignments() -> str:
        """List assignments and due dates from SQLite."""
        with session_scope(settings) as session:
            rows = session.query(Assignment).order_by(Assignment.due_at.asc()).all()
            if not rows:
                return "No assignments ingested."
            lines = []
            for row in rows:
                due = row.due_at.isoformat() if row.due_at else "no due date"
                lines.append(f"{row.id}. {row.course.code}: {row.title} ({due})")
            return "\n".join(lines)

    @mcp.tool()
    def list_announcements() -> str:
        """List triaged onQ announcements."""
        with session_scope(settings) as session:
            rows = session.query(Announcement).order_by(Announcement.posted_at.desc()).all()
            if not rows:
                return "No announcements."
            return "\n".join(f"[{a.category}] {a.course.code}: {a.title}" for a in rows)

    @mcp.tool()
    def query_syllabus(question: str) -> str:
        """RAG over indexed PDFs (syllabi, labs, rubrics) via ChromaDB."""
        return ask(question, settings=settings)

    @mcp.tool()
    def get_plan(assignment: str) -> str:
        """Deconstruct an assignment into sub-tasks with micro-deadlines."""
        return plan_and_format(assignment, settings=settings)

    @mcp.tool()
    def scaffold_assignment(assignment: str) -> str:
        """Write integrity-safe outlines or coding stubs (never finished solutions)."""
        return scaffold_and_describe(assignment, settings=settings)

    @mcp.tool()
    def get_briefing(kind: str = "morning") -> str:
        """Morning briefing or evening wrap-up assembled from SQLite."""
        which = "evening" if kind.lower().startswith("eve") else "morning"
        return render_briefing(which, settings=settings)

    @mcp.tool()
    def record_study(course: str, minutes: int, notes: str = "") -> str:
        """Record a study-habit event in SQLite."""
        with session_scope(settings) as session:
            found = find_course(session, course)
            event = record_study_session(session, minutes=minutes, notes=notes, course=found)
            label = found.code if found else "unspecified"
            return f"Logged {event.minutes} min on {label}."

    @mcp.tool()
    def draft_professor_email(topic: str, course: str = "") -> str:
        """Polite email draft with schedule context. You send it; this does not mail anyone."""
        with session_scope(settings) as session:
            return professor_email_draft(session, topic=topic, course_query=course or None)

    @mcp.tool()
    def list_habits() -> str:
        """Study minutes logged in the last 7 days, from SQLite."""
        from school_secretary.agents.memory import format_habit_summary, suggested_block

        with session_scope(settings) as session:
            return format_habit_summary(session) + "\n" + suggested_block(session)

    @mcp.tool()
    def list_subtasks(assignment: str = "") -> str:
        """Pending planner micro-deadlines, optionally filtered by assignment title."""
        with session_scope(settings) as session:
            query = session.query(SubTask).filter_by(status="pending").order_by(SubTask.due_at.asc())
            if assignment.strip():
                found = find_assignment(session, assignment)
                if found is None:
                    return f"No assignment matched {assignment!r}."
                query = query.filter_by(assignment_id=found.id)
            rows = query.limit(20).all()
            if not rows:
                return "No pending sub-tasks. Run get_plan first."
            lines = []
            for task in rows:
                due = task.due_at.isoformat() if task.due_at else "unscheduled"
                code = task.assignment.course.code if task.assignment else ""
                lines.append(f"{task.sort_order + 1}. {code}: {task.title} ({due})")
            return "\n".join(lines)

    @mcp.tool()
    def db_status() -> str:
        """Row counts from the local SQLite database (courses, assignments, habits, calendar)."""
        from school_secretary.ingest.brightspace import snapshot_counts

        with session_scope(settings) as session:
            return json.dumps(snapshot_counts(session))

    @mcp.tool()
    def sync_deadlines() -> str:
        """Push assignment and sub-task deadlines to Google Calendar, or a local ICS file."""
        from school_secretary.calendar_sync import sync_calendar

        result = sync_calendar(settings)
        return json.dumps(result)

    return mcp


def run_mcp(stdio: bool = False, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    mcp = build_mcp(settings)
    if stdio:
        mcp.run(transport="stdio")
        return
    print(
        f"MCP HTTP on http://{settings.mcp_host}:{settings.mcp_port}/mcp "
        "(streamable HTTP). Cursor stdio: uv run school-secretary mcp --stdio"
    )
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        json_response=True,
    )
