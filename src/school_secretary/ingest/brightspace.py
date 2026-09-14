from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Announcement, Assignment, Course, Document
from school_secretary.db.session import session_scope
from school_secretary.ingest.browser import cookies_from_session
from school_secretary.ingest.parser import (
    classify_document,
    parse_dropbox_item,
    parse_enrollment_item,
    parse_news_item,
)
from school_secretary.rag.extract import extract_pdf_text, file_hash

LE = "1.47"
LP = "1.47"


class IngestError(RuntimeError):
    pass


def _raw_course_dir(settings: Settings, org_unit_id: str) -> Path:
    path = settings.raw_dir / org_unit_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_raw_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def upsert_course(session, parsed: dict[str, Any], term: str = "Fall 2026") -> Course:
    course = session.query(Course).filter_by(org_unit_id=parsed["org_unit_id"]).one_or_none()
    if course is None:
        course = Course(
            org_unit_id=parsed["org_unit_id"],
            code=parsed["code"],
            name=parsed["name"],
            term=term,
        )
        session.add(course)
        session.flush()
    else:
        course.code = parsed["code"]
        course.name = parsed["name"]
        course.term = term
    return course


def upsert_announcement(session, course: Course, parsed: dict[str, Any], raw_path: str) -> Announcement:
    row = (
        session.query(Announcement)
        .filter_by(course_id=course.id, d2l_id=parsed["d2l_id"])
        .one_or_none()
    )
    if row is None:
        row = Announcement(course_id=course.id, d2l_id=parsed["d2l_id"])
        session.add(row)
    row.title = parsed["title"]
    row.body = parsed["body"]
    row.posted_at = parsed["posted_at"]
    row.raw_path = raw_path
    return row


def upsert_assignment(session, course: Course, parsed: dict[str, Any], raw_path: str) -> Assignment:
    row = (
        session.query(Assignment)
        .filter_by(course_id=course.id, d2l_id=parsed["d2l_id"])
        .one_or_none()
    )
    if row is None:
        row = Assignment(course_id=course.id, d2l_id=parsed["d2l_id"])
        session.add(row)
    row.title = parsed["title"]
    row.due_at = parsed["due_at"]
    row.instructions = parsed["instructions"]
    row.assignment_type = parsed["assignment_type"]
    row.raw_path = raw_path
    return row


def upsert_document(
    session,
    course: Course,
    path: Path,
    filename: str,
    assignment: Assignment | None = None,
    doc_type: str | None = None,
) -> Document | None:
    if not path.exists():
        return None
    digest = file_hash(path)
    row = (
        session.query(Document)
        .filter_by(course_id=course.id, filename=filename, content_hash=digest)
        .one_or_none()
    )
    text = extract_pdf_text(path) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8", errors="ignore")
    kind = doc_type or classify_document(filename, text)
    if row is None:
        row = Document(
            course_id=course.id,
            filename=filename,
            content_hash=digest,
        )
        session.add(row)
    row.path = str(path)
    row.extracted_text = text
    row.doc_type = kind
    if assignment is not None:
        row.assignment_id = assignment.id
    return row


def ingest_payloads(
    settings: Settings,
    enrollments: list[dict[str, Any]],
    news_by_org: dict[str, list[dict[str, Any]]],
    dropbox_by_org: dict[str, list[dict[str, Any]]],
    attachment_files: dict[str, Path] | None = None,
    term: str = "Fall 2026",
) -> dict[str, int]:
    """Parse Brightspace-shaped JSON into SQLite. Safe to run repeatedly."""
    attachment_files = attachment_files or {}
    counts = {"courses": 0, "announcements": 0, "assignments": 0, "documents": 0}
    with session_scope(settings) as session:
        for raw_enrollment in enrollments:
            parsed_course = parse_enrollment_item(raw_enrollment)
            if not parsed_course:
                continue
            org_id = parsed_course["org_unit_id"]
            raw_dir = _raw_course_dir(settings, org_id)
            write_raw_json(raw_dir / "enrollment.json", raw_enrollment)
            course = upsert_course(session, parsed_course, term=term)
            counts["courses"] += 1

            news_items = news_by_org.get(org_id, [])
            news_path = write_raw_json(raw_dir / "news.json", news_items)
            for item in news_items:
                parsed = parse_news_item(item)
                upsert_announcement(session, course, parsed, str(news_path))
                counts["announcements"] += 1
                for attachment in parsed["attachments"]:
                    dest = _resolve_attachment(attachment, attachment_files, raw_dir)
                    if dest:
                        upsert_document(session, course, dest, dest.name)
                        counts["documents"] += 1

            dropbox_items = dropbox_by_org.get(org_id, [])
            dropbox_path = write_raw_json(raw_dir / "dropbox.json", dropbox_items)
            for item in dropbox_items:
                parsed = parse_dropbox_item(item)
                assignment = upsert_assignment(session, course, parsed, str(dropbox_path))
                counts["assignments"] += 1
                for attachment in parsed["attachments"]:
                    dest = _resolve_attachment(attachment, attachment_files, raw_dir)
                    if dest:
                        upsert_document(
                            session,
                            course,
                            dest,
                            dest.name,
                            assignment=assignment,
                        )
                        counts["documents"] += 1
    return counts


def _resolve_attachment(
    attachment: dict[str, str],
    attachment_files: dict[str, Path],
    raw_dir: Path,
) -> Path | None:
    name = attachment.get("filename") or ""
    if not name:
        return None
    if name in attachment_files:
        source = attachment_files[name]
        dest = raw_dir / name
        if source.resolve() != dest.resolve():
            dest.write_bytes(source.read_bytes())
        return dest
    candidate = raw_dir / name
    return candidate if candidate.exists() else None


def ingest_from_raw_dir(settings: Settings | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    enrollments: list[dict[str, Any]] = []
    news_by_org: dict[str, list[dict[str, Any]]] = {}
    dropbox_by_org: dict[str, list[dict[str, Any]]] = {}
    for course_dir in sorted(settings.raw_dir.glob("*")):
        if not course_dir.is_dir():
            continue
        org_id = course_dir.name
        enrollment_path = course_dir / "enrollment.json"
        if enrollment_path.exists():
            enrollments.append(json.loads(enrollment_path.read_text(encoding="utf-8")))
        news_path = course_dir / "news.json"
        if news_path.exists():
            news_by_org[org_id] = json.loads(news_path.read_text(encoding="utf-8"))
        dropbox_path = course_dir / "dropbox.json"
        if dropbox_path.exists():
            dropbox_by_org[org_id] = json.loads(dropbox_path.read_text(encoding="utf-8"))
    return ingest_payloads(settings, enrollments, news_by_org, dropbox_by_org)


class BrightspaceClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.session_path.exists():
            raise IngestError(
                f"Missing {self.settings.storage_state_path}. "
                "Run `uv run school-secretary login` first (NetID, password, Duo). "
                "Headless refresh reuses that JSON so MFA is not prompted every time."
            )
        self.cookies = cookies_from_session(self.settings.session_path)
        self.base = self.settings.onq_base_url.rstrip("/")

    def _get(self, path: str) -> Any:
        url = self.base + path
        with httpx.Client(cookies=self.cookies, follow_redirects=True, timeout=30.0) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()

    def my_enrollments(self) -> list[dict[str, Any]]:
        payload = self._get(f"/d2l/api/lp/{LP}/enrollments/myenrollments/")
        if isinstance(payload, dict):
            return payload.get("Items") or payload.get("items") or []
        return payload

    def news(self, org_unit_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/d2l/api/le/{LE}/{org_unit_id}/news/")
        return payload if isinstance(payload, list) else payload.get("Items") or []

    def dropbox_folders(self, org_unit_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/d2l/api/le/{LE}/{org_unit_id}/dropbox/folders/")
        return payload if isinstance(payload, list) else payload.get("Items") or []

    def download_file(self, url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        full = url if url.startswith("http") else self.base + url
        with httpx.Client(cookies=self.cookies, follow_redirects=True, timeout=60.0) as client:
            response = client.get(full)
            if response.status_code >= 400:
                return False
            dest.write_bytes(response.content)
            return True


def ingest_live(settings: Settings | None = None) -> dict[str, int]:
    """Headless refresh: LE APIs with saved cookies; Playwright DOM fallback for PDFs."""
    import asyncio

    from school_secretary.ingest.browser import _headless_context, scrape_pdf_links

    settings = settings or get_settings()
    client = BrightspaceClient(settings)
    enrollments = client.my_enrollments()
    news_by_org: dict[str, list[dict[str, Any]]] = {}
    dropbox_by_org: dict[str, list[dict[str, Any]]] = {}
    attachment_files: dict[str, Path] = {}

    parsed_courses = [p for e in enrollments if (p := parse_enrollment_item(e))]
    for parsed in parsed_courses:
        org_id = parsed["org_unit_id"]
        news_by_org[org_id] = client.news(org_id)
        dropbox_by_org[org_id] = client.dropbox_folders(org_id)
        raw_dir = _raw_course_dir(settings, org_id)
        for collection in (news_by_org[org_id], dropbox_by_org[org_id]):
            for item in collection:
                from school_secretary.ingest.parser import (
                    extract_attachments_from_html,
                    extract_attachments_from_json,
                    merge_attachments,
                )

                html = ""
                body = item.get("Body") or item.get("Instructions") or {}
                if isinstance(body, dict):
                    html = body.get("Html") or ""
                for attachment in merge_attachments(
                    extract_attachments_from_json(item),
                    extract_attachments_from_html(html),
                ):
                    filename = attachment.get("filename") or f"{attachment.get('file_id')}.bin"
                    dest = raw_dir / filename
                    url = attachment.get("url") or ""
                    if url and client.download_file(url, dest):
                        attachment_files[filename] = dest

    async def _dom_fallback() -> None:
        playwright = None
        context = None
        try:
            playwright, context = await _headless_context(settings)
            page = context.pages[0] if context.pages else await context.new_page()
            for parsed in parsed_courses:
                org_id = parsed["org_unit_id"]
                raw_dir = _raw_course_dir(settings, org_id)
                course_url = f"{settings.onq_base_url.rstrip('/')}/d2l/home/{org_id}"
                try:
                    links = await scrape_pdf_links(page, course_url)
                except Exception:
                    continue
                for link in links:
                    filename = link.rsplit("/", 1)[-1].split("?")[0] or hashlib.sha256(link.encode()).hexdigest()[:12] + ".pdf"
                    if not filename.lower().endswith(".pdf"):
                        filename += ".pdf"
                    dest = raw_dir / filename
                    if dest.exists():
                        attachment_files[filename] = dest
                        continue
                    if client.download_file(link, dest):
                        attachment_files[filename] = dest
                        continue
                    try:
                        from school_secretary.ingest.browser import download_with_dom

                        await download_with_dom(page, link, dest)
                        attachment_files[filename] = dest
                    except Exception:
                        continue
        finally:
            if context is not None:
                await context.close()
            if playwright is not None:
                await playwright.stop()

    asyncio.run(_dom_fallback())
    return ingest_payloads(settings, enrollments, news_by_org, dropbox_by_org, attachment_files)
