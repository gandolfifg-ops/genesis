from __future__ import annotations

import re
from pathlib import Path

from school_secretary.agents.memory import record_study_session
from school_secretary.agents.orchestrator import (
    ask,
    find_course,
    plan_and_format,
    professor_email_draft,
    render_briefing,
    scaffold_and_describe,
)
from school_secretary.agents.persona import polish_outgoing
from school_secretary.config import Settings
from school_secretary.db.session import session_scope

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_course_docs",
            "description": "Answer a question from indexed syllabi and assignment PDFs.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_assignment",
            "description": "Break an assignment into sub-tasks with micro-deadlines.",
            "parameters": {
                "type": "object",
                "properties": {"assignment": {"type": "string"}},
                "required": ["assignment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_professor_email",
            "description": "Draft a polite email to a professor using schedule context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "course": {"type": "string"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_briefing",
            "description": "Morning briefing or evening wrap-up.",
            "parameters": {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["morning", "evening"]}},
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_study_session",
            "description": "Log study minutes against a course.",
            "parameters": {
                "type": "object",
                "properties": {
                    "course": {"type": "string"},
                    "minutes": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["course", "minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scaffold_assignment",
            "description": "Integrity-safe outline or coding stubs. Never finished solutions.",
            "parameters": {
                "type": "object",
                "properties": {"assignment": {"type": "string"}},
                "required": ["assignment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_calendar",
            "description": "Push deadlines to Google Calendar or write a local ICS file.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def dispatch_tool(name: str, arguments: dict, settings: Settings) -> str:
    if name == "query_course_docs":
        return ask(arguments["question"], settings=settings)
    if name == "plan_assignment":
        return plan_and_format(arguments["assignment"], settings=settings)
    if name == "draft_professor_email":
        with session_scope(settings) as session:
            return professor_email_draft(
                session,
                topic=arguments.get("topic", "question"),
                course_query=arguments.get("course") or None,
            )
    if name == "render_briefing":
        kind = arguments.get("kind") or "morning"
        return render_briefing(kind, settings=settings)
    if name == "record_study_session":
        with session_scope(settings) as session:
            course = find_course(session, arguments.get("course", ""))
            event = record_study_session(
                session,
                minutes=int(arguments.get("minutes") or 0),
                notes=arguments.get("notes") or "",
                course=course,
            )
            return f"Logged {event.minutes} minutes."
    if name == "scaffold_assignment":
        return scaffold_and_describe(arguments["assignment"], settings=settings)
    if name == "sync_calendar":
        from school_secretary.calendar_sync import sync_calendar

        result = sync_calendar(settings)
        extra = f" ({result['error']})" if result.get("error") else ""
        return polish_outgoing(
            f"Calendar updated — **{result['events']}** deadlines via {result['transport']}.{extra}"
        )
    return f"Unknown tool {name}"


def route_locally(text: str, settings: Settings) -> str:
    stripped = text.strip()
    lower = stripped.lower()
    if lower in {"/start", "start", "help", "/help"}:
        return (
            "**My School Secretary** is on.\n\n"
            "Commands: `/ask` `/briefing` `/evening` `/plan` `/scaffold` "
            "`/email` `/study` `/habits` `/calendar` `/done` `/snooze` `/streak` `/tasks`\n\n"
            "I never complete assignments — outlines and TODOs only."
        )
    if lower.startswith("/ask"):
        return ask(stripped[4:].strip() or stripped, settings=settings)
    if "briefing" in lower or lower in {"morning", "/briefing"}:
        return render_briefing("morning", settings=settings)
    if "evening" in lower or "wrap" in lower:
        return render_briefing("evening", settings=settings)
    if lower.startswith("/plan") or lower.startswith("plan "):
        target = re.sub(r"^/?plan\s*", "", stripped, flags=re.I)
        return plan_and_format(target or "Lab 2", settings=settings)
    if "email" in lower or lower.startswith("/email"):
        topic = re.sub(r"^/?email\s*", "", stripped, flags=re.I) or "office hours"
        with session_scope(settings) as session:
            return professor_email_draft(session, topic=topic, course_query=None)
    if lower.startswith("/scaffold") or "scaffold" in lower:
        target = re.sub(r"^/?scaffold\s*", "", stripped, flags=re.I) or "Lab 2"
        return scaffold_and_describe(target, settings=settings)
    if lower.startswith("/calendar") or lower == "calendar":
        from school_secretary.calendar_sync import sync_calendar

        result = sync_calendar(settings)
        extra = f" ({result['error']})" if result.get("error") else ""
        return polish_outgoing(
            f"Calendar updated — **{result['events']}** deadlines via {result['transport']}.{extra}"
        )
    if lower.startswith("/habits") or lower in {"habits", "streak", "/streak"}:
        from school_secretary.agents.tasks import format_streak

        return format_streak(settings)
    if lower.startswith("/tasks") or lower == "tasks":
        from school_secretary.agents.tasks import format_task_list

        return format_task_list(settings)
    if lower.startswith("/done") or lower.startswith("done "):
        from school_secretary.agents.tasks import mark_done

        target = re.sub(r"^/?done\s*", "", stripped, flags=re.I)
        return mark_done(settings, target)
    if lower.startswith("/snooze") or lower.startswith("snooze "):
        from school_secretary.agents.tasks import snooze_task

        target = re.sub(r"^/?snooze\s*", "", stripped, flags=re.I)
        return snooze_task(settings, target)
    if lower.startswith("/study") or lower.startswith("study "):
        parts = stripped.split()
        minutes = 30
        for part in parts:
            if part.isdigit():
                minutes = int(part)
                break
        course_q = next((p for p in parts if any(ch.isalpha() for ch in p) and p.lower() not in {"study", "/study"}), "")
        with session_scope(settings) as session:
            course = find_course(session, course_q) if course_q else None
            record_study_session(session, minutes=minutes, notes=stripped, course=course)
        return f"Logged {minutes} minutes" + (f" on {course_q}" if course_q else "") + "."
    if "?" in stripped or any(word in lower for word in ("late", "penalty", "syllabus", "when is", "due")):
        return ask(stripped, settings=settings)
    return ask(stripped, settings=settings)


def handle_user_text(text: str, settings: Settings) -> str:
    stripped = (text or "").strip()
    lower = stripped.lower()
    if (
        lower.startswith("/")
        or lower in {"help", "start", "briefing", "morning", "evening", "habits", "calendar", "streak", "tasks"}
        or lower.startswith(("plan ", "email", "scaffold", "study ", "done ", "snooze "))
        or "briefing" in lower
        or "wrap" in lower
    ):
        return polish_outgoing(route_locally(stripped, settings))
    return polish_outgoing(ask(stripped, settings=settings))


def remember_chat_id(path: Path, chat_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(chat_id), encoding="utf-8")


def load_chat_id(path: Path, fallback: str = "") -> str:
    if fallback:
        return fallback
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""
