from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pathlib import Path

from sqlalchemy.orm import Session

from school_secretary.agents.memory import format_habit_summary, record_study_session, study_streak_days
from school_secretary.config import Settings, get_settings
from school_secretary.db.live import live_course_ids
from school_secretary.db.models import Assignment, SubTask
from school_secretary.db.session import session_scope

SNOOZE_HOURS = 24


def _tz(settings: Settings | None = None) -> ZoneInfo:
    return ZoneInfo((settings or get_settings()).timezone)


def snooze_path(settings: Settings) -> Path:
    return settings.data_dir / "snoozes.json"


def _load_snoozes(settings: Settings) -> dict[str, str]:
    path = settings.data_dir / "snoozes.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_snoozes(settings: Settings, payload: dict[str, str]) -> None:
    settings.ensure_dirs()
    (settings.data_dir / "snoozes.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_snoozed(settings: Settings, assignment_id: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(tz=_tz(settings))
    until = _load_snoozes(settings).get(str(assignment_id))
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(until)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz(settings))
    return dt > now


def parse_task_ref(raw: str) -> tuple[str, int] | None:
    text = (raw or "").strip()
    match = re.match(r"^(a|asg|assignment)?\s*[-:]?\s*(\d+)$", text, re.IGNORECASE)
    if not match:
        return None
    kind = "assignment" if match.group(1) else "subtask"
    return kind, int(match.group(2))


def mark_done(settings: Settings, ref: str) -> str:
    parsed = parse_task_ref(ref)
    if not parsed:
        return "Usage: `/done 12` (subtask id) or `/done a12` (whole assignment)."
    kind, ident = parsed
    with session_scope(settings) as session:
        if kind == "assignment":
            assignment = session.get(Assignment, ident)
            if assignment is None:
                return f"No assignment #{ident}."
            tasks = list(assignment.subtasks)
            if not tasks:
                return f"**{assignment.course.code}:** {assignment.title} has no planned steps yet. `/plan {assignment.title}`"
            for task in tasks:
                task.status = "done"
            return f"Checked off **{assignment.course.code}: {assignment.title}** ({len(tasks)} steps)."
        task = session.get(SubTask, ident)
        if task is None:
            return f"No subtask #{ident}."
        task.status = "done"
        code = task.assignment.course.code if task.assignment else ""
        return f"Done: **{code}** {task.title}."


def snooze_task(settings: Settings, ref: str, hours: int = SNOOZE_HOURS) -> str:
    parsed = parse_task_ref(ref)
    if not parsed:
        return "Usage: `/snooze 12` (subtask) or `/snooze a12` (assignment deadline alert)."
    kind, ident = parsed
    until = datetime.now(tz=_tz(settings)) + timedelta(hours=hours)
    with session_scope(settings) as session:
        if kind == "assignment":
            assignment = session.get(Assignment, ident)
            if assignment is None:
                return f"No assignment #{ident}."
            snoozes = _load_snoozes(settings)
            snoozes[str(assignment.id)] = until.isoformat()
            _save_snoozes(settings, snoozes)
            return (
                f"Snoozed deadline alerts for **{assignment.course.code}: {assignment.title}** "
                f"until {until.strftime('%a %b %d %H:%M')}."
            )
        task = session.get(SubTask, ident)
        if task is None:
            return f"No subtask #{ident}."
        if task.due_at is not None:
            due = task.due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=_tz(settings))
            task.due_at = due + timedelta(hours=hours)
        snoozes = _load_snoozes(settings)
        snoozes[str(task.assignment_id)] = until.isoformat()
        _save_snoozes(settings, snoozes)
        code = task.assignment.course.code if task.assignment else ""
        return f"Snoozed **{code}** {task.title} by {hours}h."


def list_open_tasks(session: Session, limit: int = 8) -> list[SubTask]:
    query = (
        session.query(SubTask)
        .filter(SubTask.status != "done")
        .order_by(SubTask.due_at.asc(), SubTask.id.asc())
    )
    live_ids = live_course_ids(session)
    if live_ids:
        query = query.join(Assignment).filter(Assignment.course_id.in_(live_ids))
    return query.limit(limit).all()


def format_task_list(settings: Settings) -> str:
    with session_scope(settings) as session:
        tasks = list_open_tasks(session)
        if not tasks:
            return "Nothing open. `/plan` an assignment, or you are clear for now."
        lines = ["**Open tasks** — tap Done / Snooze, or `/done 12` / `/snooze 12`", ""]
        for task in tasks:
            code = task.assignment.course.code if task.assignment else ""
            due = task.due_at.strftime("%a %b %d %H:%M") if task.due_at else "unscheduled"
            lines.append(f"`{task.id}` **{code}:** {task.title} ({due})")
        return "\n".join(lines)


def format_streak(settings: Settings, now: datetime | None = None) -> str:
    with session_scope(settings) as session:
        summary = format_habit_summary(session, now=now)
        streak = study_streak_days(session, now=now)
        return f"{summary}\n\nTap **Log 45 min** or `/study 45` to keep the {streak}-day streak honest."


def log_streak_minutes(settings: Settings, minutes: int = 45) -> str:
    with session_scope(settings) as session:
        record_study_session(session, minutes=minutes, notes="telegram streak")
        streak = study_streak_days(session)
        return f"Logged {minutes} min. Streak: **{streak}** day(s)."


def task_keyboard_rows(task_ids: list[int]) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    for ident in task_ids[:6]:
        rows.append(
            [
                (f"Done {ident}", f"done:{ident}"),
                (f"Snooze {ident}", f"snooze:{ident}"),
            ]
        )
    rows.append([("Log 45 min", "study:45"), ("Streak", "streak"), ("Open tasks", "tasks")])
    return rows
