from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

DUE_PATTERNS = [
    re.compile(r"\bdue(?:\s+date)?\s*[:\-]?\s*([^<\n]+)", re.IGNORECASE),
    re.compile(r"\bdue on\s+([^<\n]+)", re.IGNORECASE),
    re.compile(r"\bdeadline\s*[:\-]?\s*([^<\n]+)", re.IGNORECASE),
    re.compile(r"\bavailable until\s*[:\-]?\s*([^<\n]+)", re.IGNORECASE),
    re.compile(r"\bcloses?\s+on\s+([^<\n]+)", re.IGNORECASE),
]
MONTH_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}"
    r"(?:\s+\d{1,2}:\d{2}\s*(?:am|pm)?)?",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?)"
)
US_DATE_RE = re.compile(
    r"\b(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}\s*(?:am|pm)?)?)",
    re.IGNORECASE,
)
NOISY_TITLE_EXACT = {
    "not submitted",
    "submitted",
    "completion",
    "evaluation",
    "new",
    "folder",
    "feedback",
    "score",
    "grade",
    "completed",
    "in progress",
    "read only",
    "1 submission, 1 file",
    "0 submissions, 0 files",
    "1 submission, 0 files",
    "0 submissions, 1 file",
}
NOISY_TITLE_RE = re.compile(
    r"^\d+\s+submissions?(?:,\s+\d+\s+files?)?$"
    r"|^\d+\s+files?$"
    r"|^submissions?$"
    r"|^unread$",
    re.IGNORECASE,
)

WORD_COUNT_RE = re.compile(r"(\d{3,5})\s*(?:word|words)\b", re.IGNORECASE)
ATTACHMENT_EXT = (".pdf", ".docx", ".doc", ".txt")


def parse_datetime(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        dt = date_parser.parse(text, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text("\n", strip=True)


def body_text(payload: dict[str, Any] | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return html_to_text(payload) if "<" in payload else payload
    html = payload.get("Html") or payload.get("html") or ""
    text = payload.get("Text") or payload.get("text") or ""
    return (text or html_to_text(html)).strip()


def extract_due_from_text(text: str) -> datetime | None:
    blob = text or ""
    for pattern in DUE_PATTERNS:
        match = pattern.search(blob)
        if match:
            candidate = match.group(1).split(". ")[0].strip()
            dt = parse_datetime(candidate)
            if dt:
                return dt
    for pattern in (MONTH_DATE_RE, ISO_DATE_RE, US_DATE_RE):
        match = pattern.search(blob)
        if match:
            dt = parse_datetime(match.group(0))
            if dt:
                return dt
    return None


def is_noisy_assignment_title(title: str | None) -> bool:
    """True for dropbox status cells mistaken for folder names."""
    text = " ".join((title or "").split())
    if not text or len(text) < 3:
        return True
    lowered = text.lower().strip(" .")
    if lowered in NOISY_TITLE_EXACT or lowered in _SKIP_NAV:
        return True
    if NOISY_TITLE_RE.match(lowered):
        return True
    return False


_SKIP_NAV = {
    "home",
    "my courses",
    "view all",
    "view all courses",
    "announcements",
    "assignments",
    "dropbox",
    "content",
    "grades",
    "title",
    "name",
    "due date",
    "more actions",
}

KEEP_RE = re.compile(
    r"\b(syllabus|rubric|lab\b|labs\b|quiz|quizzes|test\b|tests\b|exam|midterm|final\b|"
    r"assignment|project|essay|dropbox|deadline)\b",
    re.IGNORECASE,
)
RESIDENCE_RE = re.compile(
    r"\b(residence|housing contract|lease agreement|dorm(itory)?|tenant|residence agreement|"
    r"room(s)? contract|meal plan)\b",
    re.IGNORECASE,
)
HOMEWORK_PSET_RE = re.compile(
    r"\b(homework|problem set|p-?sets?|worksheet|practice problems?)\b",
    re.IGNORECASE,
)
READING_RE = re.compile(
    r"\b((week\s+\d+\s+)?readings?|required reading|chapter\s+\d+|textbook|"
    r"lecture slides|course notes|weekly reading)\b",
    re.IGNORECASE,
)


def is_clutter_material(title: str | None, text: str = "", filename: str = "") -> bool:
    """Residence contracts, general readings, and homework problem sets — skip these."""
    heading = f"{title or ''} {filename or ''}"
    blob = f"{heading}\n{(text or '')[:1500]}"
    if RESIDENCE_RE.search(blob) and not KEEP_RE.search(heading):
        return True
    if HOMEWORK_PSET_RE.search(heading) and not re.search(
        r"\b(lab\b|quiz|test\b|exam|project|essay|syllabus)\b", heading, re.IGNORECASE
    ):
        return True
    if READING_RE.search(heading) and not KEEP_RE.search(heading):
        return True
    return False


def is_target_material(
    title: str | None,
    text: str = "",
    filename: str = "",
    *,
    has_due: bool = False,
    kind: str = "",
) -> bool:
    """Announcements, assignment/project dropboxes, labs, quizzes, tests, syllabi, due dates."""
    if is_noisy_assignment_title(title) and kind != "announcement":
        return False
    heading = f"{title or ''} {filename or ''}"
    blob = f"{heading}\n{(text or '')[:1500]}"
    if is_clutter_material(title, text, filename):
        return False
    if kind == "announcement":
        return True
    if KEEP_RE.search(blob):
        return True
    if has_due or extract_due_from_text(blob) is not None:
        return True
    return False


def is_target_assignment(title: str | None, instructions: str = "", due_at=None) -> bool:
    return is_target_material(title, instructions, has_due=due_at is not None, kind="assignment")


def is_target_announcement(title: str | None, body: str = "") -> bool:
    return is_target_material(title, body, kind="announcement")


def is_target_content_topic(title: str | None) -> bool:
    """Follow content-module links only when they look like syllabus/lab/quiz/assignment."""
    if is_clutter_material(title, filename=title or ""):
        return False
    return bool(KEEP_RE.search(title or "")) or extract_due_from_text(title or "") is not None


def should_download_file(label: str, href: str = "", *, strict: bool = False) -> bool:
    name = (label or "").strip() or (href or "").rsplit("/", 1)[-1].split("?")[0]
    if is_clutter_material(name, filename=name):
        return False
    if not strict:
        return True
    return is_target_material(name, filename=name or href)


def extract_attachments_from_json(item: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for attachment in item.get("Attachments") or item.get("attachments") or []:
        name = (
            attachment.get("FileName")
            or attachment.get("fileName")
            or attachment.get("Name")
            or ""
        )
        file_id = str(attachment.get("FileId") or attachment.get("fileId") or "")
        url = attachment.get("Url") or attachment.get("url") or ""
        if name or url:
            found.append({"file_id": file_id, "filename": name, "url": url})
    return found


def extract_attachments_from_html(html: str | None) -> list[dict[str, str]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, str]] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        label = anchor.get_text(" ", strip=True)
        lowered = href.lower()
        if lowered.endswith(ATTACHMENT_EXT) or "download" in lowered or ".pdf" in lowered:
            filename = label if label.lower().endswith(ATTACHMENT_EXT) else href.rsplit("/", 1)[-1]
            found.append({"file_id": "", "filename": filename, "url": href})
    return found


def merge_attachments(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    for group in groups:
        for item in group:
            key = (item.get("filename") or item.get("url") or "").lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def infer_assignment_type(title: str, instructions: str) -> str:
    blob = f"{title}\n{instructions}".lower()
    if any(word in blob for word in ("essay", "paper", "word count", "thesis")):
        return "essay"
    if any(word in blob for word in ("lab", "implement", "unit test", "github", "code", "program")):
        return "coding_lab"
    return "other"


def extract_word_count(text: str) -> int | None:
    match = WORD_COUNT_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def classify_document(filename: str, text: str = "") -> str:
    blob = f"{filename}\n{text[:2000]}".lower()
    if is_clutter_material(filename, text, filename):
        return "clutter"
    if "syllabus" in blob:
        return "syllabus"
    if "rubric" in blob:
        return "rubric"
    if any(word in blob for word in ("quiz", "midterm", "exam", "test ")):
        return "quiz"
    if "lab" in blob:
        return "lab"
    if any(word in blob for word in ("assignment", "project", "essay")):
        return "assignment"
    return "handout"


def parse_news_item(item: dict[str, Any]) -> dict[str, Any]:
    html = ""
    body = item.get("Body") or {}
    if isinstance(body, dict):
        html = body.get("Html") or ""
    elif isinstance(body, str):
        html = body
    text = body_text(body)
    attachments = merge_attachments(
        extract_attachments_from_json(item),
        extract_attachments_from_html(html),
    )
    return {
        "d2l_id": str(item.get("Id") or item.get("id") or ""),
        "title": item.get("Title") or item.get("title") or "Untitled announcement",
        "body": text,
        "posted_at": parse_datetime(item.get("StartDate") or item.get("startDate")),
        "attachments": attachments,
    }


def parse_dropbox_item(item: dict[str, Any]) -> dict[str, Any]:
    instructions_payload = item.get("Instructions") or item.get("instructions") or {}
    html = ""
    if isinstance(instructions_payload, dict):
        html = instructions_payload.get("Html") or ""
    instructions = body_text(instructions_payload)
    due = parse_datetime(item.get("DueDate") or item.get("dueDate"))
    title = item.get("Name") or item.get("name") or "Untitled assignment"
    if due is None:
        due = extract_due_from_text(f"{title}\n{instructions}")
    attachments = merge_attachments(
        extract_attachments_from_json(item),
        extract_attachments_from_html(html),
    )
    return {
        "d2l_id": str(item.get("Id") or item.get("id") or ""),
        "title": title,
        "due_at": due,
        "instructions": instructions,
        "assignment_type": infer_assignment_type(title, instructions),
        "attachments": attachments,
        "word_count": extract_word_count(instructions),
    }


def parse_enrollment_item(item: dict[str, Any]) -> dict[str, Any] | None:
    org = item.get("OrgUnit") or item.get("orgUnit") or item
    org_type = org.get("Type") or {}
    type_code = (org_type.get("Code") or org_type.get("code") or "").lower()
    type_id = org_type.get("Id") or org_type.get("id")
    # Brightspace: Course Offering type id is typically 3
    name = org.get("Name") or org.get("name") or ""
    code = org.get("Code") or org.get("code") or ""
    org_id = org.get("Id") or org.get("id")
    if org_id is None:
        return None
    if type_code and type_code not in {"course offering", "courseoffering", "course"}:
        if type_id not in {3, "3", None}:
            return None
    if not name and not code:
        return None
    return {
        "org_unit_id": str(org_id),
        "code": _normalize_code(code or name),
        "name": name or code,
    }


def _normalize_code(raw: str) -> str:
    match = re.search(r"([A-Z]{3,4})\s*-?\s*(\d{3}[A-Z]?)", raw.upper())
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return raw.strip()
