from __future__ import annotations

import sys
from typing import Optional

import typer

from school_secretary.config import get_settings
from school_secretary.db.session import init_db

app = typer.Typer(
    add_completion=False,
    help="My School Secretary — Queen's onQ executive assistant (fixtures work offline).",
)


def main() -> None:
    app()


@app.command()
def demo() -> None:
    """Offline end-to-end: ingest fixtures → RAG → planner → scaffold → briefing."""
    from school_secretary.agents.orchestrator import (
        ask,
        plan_and_format,
        render_briefing,
        run_agents,
        scaffold_and_describe,
    )
    from school_secretary.ingest.fixtures import DEMO_NOW, ingest_fixtures

    settings = get_settings()
    print("== 1. Ingest fixture courses, announcements, PDFs, study habits ==")
    counts = ingest_fixtures(settings)
    print(counts)
    print("\n== 2. Triage + plan + index PDFs into ChromaDB ==")
    print(run_agents(settings))
    print("\n== 3. RAG: late penalties (course-scoped) ==")
    print("CISC 235:", ask("What is the late penalty for CISC 235?", settings=settings))
    print("CISC 365:", ask("What is the late penalty for CISC 365?", settings=settings))
    print("\n== 4. Planner: Lab 2 binary search trees ==")
    print(plan_and_format("Binary Search", settings=settings))
    print("\n== 5. Execution/Drafting scaffold (stubs only) ==")
    print(scaffold_and_describe("Binary Search", settings=settings))
    print("\n== 6. Morning briefing ==")
    print(render_briefing("morning", settings=settings, now=DEMO_NOW))
    print("\n== 7. Calendar push (ICS mock unless Google OAuth is set) ==")
    from school_secretary.calendar_sync import sync_calendar

    print(sync_calendar(settings))
    print("\nDemo complete. Next: `uv run school-secretary mcp` or see README for Telegram / onQ login.")


@app.command()
def ingest(
    fixtures: bool = typer.Option(True, "--fixtures/--live", help="Fixture JSON/PDFs (default) or live onQ."),
    headed: bool = typer.Option(
        True,
        "--headed/--headless",
        help="Visible Chromium for --live (default). --headless invalidates a headed onQ session.",
    ),
) -> None:
    """Ingest Brightspace data into SQLite. Default is offline fixtures."""
    settings = get_settings()
    init_db(settings)
    if fixtures:
        from school_secretary.ingest.fixtures import ingest_fixtures

        print(ingest_fixtures(settings))
        return
    from school_secretary.ingest.brightspace import ingest_live, IngestError

    try:
        print(ingest_live(settings, headed=headed))
    except IngestError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


@app.command()
def login() -> None:
    """Queen's SSO in a visible Chromium window; then live LE/LP ingest in that same session."""
    from school_secretary.ingest.browser import login as browser_login

    browser_login(get_settings())


@app.command()
def query(question: str) -> None:
    """Ask a syllabus / policy question (RAG over indexed PDFs)."""
    from school_secretary.agents.orchestrator import ask

    print(ask(question, settings=get_settings()))


@app.command()
def plan(assignment: str = "Lab 2") -> None:
    """Deconstruct an assignment into sub-tasks with micro-deadlines."""
    from school_secretary.agents.orchestrator import plan_and_format

    print(plan_and_format(assignment, settings=get_settings()))


@app.command()
def scaffold(assignment: str = "Lab 2") -> None:
    """Write outlines or coding TODO/test shells. Never finished solutions."""
    from school_secretary.agents.orchestrator import scaffold_and_describe

    print(scaffold_and_describe(assignment, settings=get_settings()))


@app.command()
def briefing(kind: str = "morning") -> None:
    """Print a morning briefing or evening wrap-up."""
    from school_secretary.agents.orchestrator import render_briefing

    which = "evening" if kind.lower().startswith("eve") else "morning"
    print(render_briefing(which, settings=get_settings()))


@app.command()
def study(
    course: str = typer.Option(..., "--course", "-c"),
    minutes: int = typer.Option(..., "--minutes", "-m"),
    notes: str = typer.Option("", "--notes", "-n"),
) -> None:
    """Log a study-habit session in SQLite."""
    from school_secretary.agents.memory import record_study_session
    from school_secretary.agents.orchestrator import find_course
    from school_secretary.db.session import session_scope

    settings = get_settings()
    with session_scope(settings) as session:
        found = find_course(session, course)
        event = record_study_session(session, minutes=minutes, notes=notes, course=found)
        label = found.code if found else course
        print(f"Logged {event.minutes} min on {label}.")


@app.command()
def email(
    topic: str = typer.Option(..., "--topic", "-t"),
    course: Optional[str] = typer.Option(None, "--course", "-c"),
) -> None:
    """Draft a polite professor email with schedule context (does not send)."""
    from school_secretary.agents.orchestrator import professor_email_draft
    from school_secretary.db.session import session_scope

    settings = get_settings()
    with session_scope(settings) as session:
        print(professor_email_draft(session, topic=topic, course_query=course))


@app.command()
def mcp(
    stdio: bool = typer.Option(False, "--stdio", help="stdio transport for Cursor; default is HTTP."),
) -> None:
    """Start the local MCP server (SQLite + ChromaDB tools)."""
    from school_secretary.mcp_app.server import run_mcp

    run_mcp(stdio=stdio, settings=get_settings())


@app.command()
def telegram() -> None:
    """Poll Telegram. Exits with a setup message if TELEGRAM_BOT_TOKEN is missing."""
    from school_secretary.telegram_app.bot import run_bot

    run_bot(get_settings())


@app.command()
def whatsapp() -> None:
    """WhatsApp Cloud webhook, or a local mock on port 43148 if credentials are missing."""
    from school_secretary.whatsapp_app.server import run_whatsapp

    run_whatsapp(get_settings())


@app.command("calendar-sync")
def calendar_sync_cmd() -> None:
    """Push assignment/sub-task deadlines to Google Calendar, or write a local ICS file."""
    from school_secretary.calendar_sync import sync_calendar

    print(sync_calendar(get_settings()))


@app.command()
def habits() -> None:
    """Print this week's study minutes, streak, and a suggested block."""
    from school_secretary.agents.memory import format_habit_summary, suggested_block
    from school_secretary.db.session import session_scope

    settings = get_settings()
    init_db(settings)
    with session_scope(settings) as session:
        print(format_habit_summary(session))
        print(suggested_block(session))


@app.command()
def status() -> None:
    """Print SQLite row counts and live vs fixture course codes (no PII)."""
    from school_secretary.db.live import FIXTURE_ORG_UNIT_IDS, live_course_ids
    from school_secretary.db.models import Course
    from school_secretary.db.session import session_scope
    from school_secretary.ingest.brightspace import snapshot_counts

    settings = get_settings()
    init_db(settings)
    with session_scope(settings) as session:
        print(snapshot_counts(session))
        live_ids = live_course_ids(session)
        rows = session.query(Course).order_by(Course.code).all()
        live_codes = [c.code for c in rows if c.org_unit_id not in FIXTURE_ORG_UNIT_IDS]
        fixture_codes = [c.code for c in rows if c.org_unit_id in FIXTURE_ORG_UNIT_IDS]
        print({"live_course_codes": live_codes, "fixture_course_codes": fixture_codes})
        if live_ids is None and not rows:
            print("SQLite has schema but no courses. Run `login` or `ingest --live` on WSL (headed), or `ingest --fixtures`.")


@app.command("index-docs")
def index_docs() -> None:
    """Re-embed documents into ChromaDB."""
    from school_secretary.rag.index import index_documents

    print({"indexed": index_documents(get_settings(), force=True)})
