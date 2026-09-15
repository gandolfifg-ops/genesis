from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from school_secretary.agents.drafting import parse_rubric_weights
from school_secretary.agents.memory import course_minutes, habit_note_for_course
from school_secretary.config import get_settings
from school_secretary.db.models import Assignment, Document, SubTask
from school_secretary.ingest.parser import extract_word_count

GENERIC_FILLER = (
    "read the assignment instructions",
    "break the deliverable into the required parts",
    "produce a first version of each part yourself",
)

NUMBERED_ITEM_RE = re.compile(
    r"(?im)^\s*(?:(?:part|section|problem|question)\s+([A-Z]|\d+)|(\d+)|([A-Z]))[).:\-]\s+(.{8,140})"
)
BULLET_RE = re.compile(r"(?im)^\s*(?:[-*•]|–)\s+(.{8,140})")
OP_RE = re.compile(
    r"\b(insert|search|delete|sort|trace|implement|analyse|analyze|simulate|derive)\b",
    re.IGNORECASE,
)


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(_tz())
    return dt.astimezone(_tz())


def _assignment_corpus(session: Session, assignment: Assignment) -> str:
    parts = [assignment.title or "", assignment.instructions or ""]
    docs = session.query(Document).filter_by(assignment_id=assignment.id).all()
    if not docs:
        docs = session.query(Document).filter_by(course_id=assignment.course_id).all()
        title_bits = re.findall(r"[a-z0-9]{3,}", (assignment.title or "").lower())
        if title_bits:
            docs = [
                doc
                for doc in docs
                if any(bit in (doc.filename + " " + (doc.extracted_text or "")[:400]).lower() for bit in title_bits[:4])
            ]
    for document in docs:
        parts.append(document.extracted_text or "")
    return "\n".join(parts)


def _clean_clause(text: str) -> str:
    clause = re.sub(r"\s+", " ", text).strip(" .;:")
    if clause and clause[0].islower():
        clause = clause[0].upper() + clause[1:]
    return clause[:120]


def _actionize(clause: str, assignment_type: str) -> str:
    lower = clause.lower()
    if assignment_type == "essay" or any(word in lower for word in ("essay", "thesis", "word count")):
        if "problem statement" in lower:
            return "Draft Section 1 problem statement"
        if "rubric" in lower:
            return clause if clause.lower().startswith("review") else f"Review rubric: {clause}"
        return f"Outline: {clause} (no finished prose)"
    if any(word in lower for word in ("cad", "drawing", "solidworks", "sketch")):
        return f"Complete CAD drawing for {clause}" if "cad" not in lower else _clean_clause(clause)
    if lower.startswith(("implement", "write", "code", "build")):
        return f"{clause} yourself (no generated solutions)"
    if lower.startswith(("draft", "outline", "review", "complete", "gather", "fill")):
        return clause
    return f"Complete: {clause} (you write the work)"


def steps_from_text(title: str, corpus: str, assignment_type: str = "other") -> list[str]:
    """Specific subtasks from the assignment title and downloaded file text."""
    blob = f"{title}\n{corpus}"
    steps: list[str] = []
    seen: set[str] = set()

    def add(step: str) -> None:
        compact = re.sub(r"\s+", " ", step).strip()
        if not compact:
            return
        key = compact.lower()
        if any(filler in key for filler in GENERIC_FILLER):
            return
        if key in seen:
            return
        seen.add(key)
        steps.append(compact)

    for match in NUMBERED_ITEM_RE.finditer(blob):
        body = _clean_clause(match.group(4))
        label = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if re.match(r"^(part|section)$", body.lower()):
            continue
        if "problem statement" in body.lower():
            add("Draft Section 1 problem statement")
            continue
        if label and label.upper() in "ABCDEFGH":
            add(_actionize(f"Part {label.upper()}: {body}", assignment_type))
        elif label and label.isdigit() and body.lower().startswith("section"):
            add(_actionize(body, assignment_type))
        else:
            add(_actionize(body, assignment_type))
        if len(steps) >= 6:
            break

    if len(steps) < 3:
        for match in BULLET_RE.finditer(blob):
            add(_actionize(_clean_clause(match.group(1)), assignment_type))
            if len(steps) >= 6:
                break

    lower_blob = blob.lower()
    if re.search(r"\bcad\b|solidworks|orthographic|drawing", lower_blob):
        add("Complete CAD drawing for part A")
    if "problem statement" in lower_blob:
        add("Draft Section 1 problem statement")
    rubric = parse_rubric_weights(blob)
    if rubric:
        labels = ", ".join(f"{label} ({pct}%)" for label, pct in rubric[:4])
        add(f"Review rubric requirements: {labels}")
    elif "rubric" in lower_blob:
        add("Review rubric requirements")

    ops = []
    for match in OP_RE.finditer(blob):
        word = match.group(1).lower()
        if word not in ops and word not in {"implement", "analyse", "analyze"}:
            ops.append(word)
    if assignment_type == "coding_lab" or "lab" in (title or "").lower() or "implement" in lower_blob:
        if ops:
            add(f"Implement {', '.join(ops[:4])} yourself (no generated solutions)")
        elif "implement" in lower_blob:
            snippet = title or "the required operations"
            add(f"Implement {snippet} yourself (no generated solutions)")
        if "test" in lower_blob or "unit" in lower_blob:
            add("Fill in the unit-test shells and run them")

    words = extract_word_count(blob)
    if assignment_type == "essay" or words:
        add(f"Draft a section outline only ({words or 1500} words from the rubric — no full prose)")

    if not steps:
        short = (title or "this assignment").strip()
        add(f"Outline the required parts of {short}")
        add(f"Complete {short} yourself (scaffolding only — you write the work)")

    add("Submit with a 24-hour buffer")
    return steps[:8]


def plan_assignment(session: Session, assignment: Assignment, now: datetime | None = None) -> list[SubTask]:
    now = now or datetime.now(tz=_tz())
    due = _as_local(assignment.due_at) or now + timedelta(days=14)
    corpus = _assignment_corpus(session, assignment)
    steps = steps_from_text(assignment.title, corpus, assignment.assignment_type)
    studied = course_minutes(session, assignment.course_id, days=7, now=now)
    extra = 1.5 if studied <= 0 else (0.2 if studied >= 90 else 0.5)
    span = max((due - now).total_seconds(), 3600)
    existing = {
        row.sort_order: row
        for row in session.query(SubTask).filter_by(assignment_id=assignment.id).all()
    }
    created: list[SubTask] = []
    kept: set[int] = set()
    for index, title in enumerate(steps):
        fraction = (index + 1) / (len(steps) + extra)
        micro_due = now + timedelta(seconds=span * fraction)
        if micro_due > due - timedelta(hours=4) and index < len(steps) - 1:
            micro_due = due - timedelta(hours=12 * (len(steps) - index))
        row = existing.get(index)
        if row is None:
            row = SubTask(
                assignment_id=assignment.id,
                title=title,
                due_at=micro_due,
                status="pending",
                sort_order=index,
            )
            session.add(row)
        else:
            row.title = title
            row.sort_order = index
            if row.status != "done":
                row.due_at = micro_due
        created.append(row)
        kept.add(index)
    for order, row in existing.items():
        if order not in kept:
            session.delete(row)
    assignment.planned = True
    session.flush()
    return created


def plan_unplanned(session: Session, now: datetime | None = None) -> list[Assignment]:
    rows = session.query(Assignment).filter_by(planned=False).all()
    for assignment in rows:
        plan_assignment(session, assignment, now=now)
    return rows


def replan_all(session: Session, now: datetime | None = None) -> int:
    rows = session.query(Assignment).all()
    for assignment in rows:
        plan_assignment(session, assignment, now=now)
    return len(rows)


def format_plan(assignment: Assignment) -> str:
    from sqlalchemy.orm import object_session

    tz = _tz()
    due = _as_local(assignment.due_at)
    due_s = due.strftime("%a %b %d %H:%M") if due else "no due date on file"
    lines = [
        f"**Plan — {assignment.course.code} · {assignment.title}**",
        f"Due {due_s} · {assignment.assignment_type.replace('_', ' ')}",
        "_Integrity: this is a work plan only. You write the actual work._",
        "",
    ]
    sess = object_session(assignment)
    if sess is not None:
        lines.append(habit_note_for_course(sess, assignment.course_id))
        lines.append("")
    lines.append("### Next steps")
    for task in assignment.subtasks:
        due_at = _as_local(task.due_at)
        due_label = due_at.astimezone(tz).strftime("%a %b %d %H:%M") if due_at else "unscheduled"
        lines.append(f"{task.sort_order + 1}. **{task.title}** — {due_label}")
    return "\n".join(lines)
