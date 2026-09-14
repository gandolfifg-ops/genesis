from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from school_secretary.config import Settings, get_settings
from school_secretary.db.models import (
    Announcement,
    Assignment,
    CalendarEvent,
    Course,
    Document,
    StudyHabitEvent,
    SubTask,
)
from school_secretary.db.session import session_scope
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


SESSION_EXPIRED = (
    "onQ session expired or not authorized. "
    "Re-run `uv run school-secretary login` on WSL (NetID, password, Duo). "
    "Headless ingest will not open a browser."
)


def _session_expired_error(status: int, content_type: str) -> IngestError:
    ctype = (content_type or "").split(";")[0] or "unknown"
    return IngestError(f"{SESSION_EXPIRED} (HTTP {status}, {ctype})")


def attachment_api_path(kind: str, org_id: str, parent_id: str, file_id: str) -> str:
    """Brightspace LE path for a news or dropbox attachment. No query secrets."""
    if kind == "news":
        return f"/d2l/api/le/{LE}/{org_id}/news/{parent_id}/attachments/{file_id}"
    return f"/d2l/api/le/{LE}/{org_id}/dropbox/folders/{parent_id}/attachments/{file_id}"


def snapshot_counts(session) -> dict[str, int]:
    return {
        "courses": session.query(Course).count(),
        "announcements": session.query(Announcement).count(),
        "assignments": session.query(Assignment).count(),
        "documents": session.query(Document).count(),
        "subtasks": session.query(SubTask).count(),
        "habits": session.query(StudyHabitEvent).count(),
        "calendar_events": session.query(CalendarEvent).count(),
    }


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
    rows = (
        session.query(Document)
        .filter_by(course_id=course.id, filename=filename)
        .order_by(Document.id.asc())
        .all()
    )
    row = rows[0] if rows else None
    for duplicate in rows[1:]:
        session.delete(duplicate)
    text = extract_pdf_text(path) if path.suffix.lower() == ".pdf" else path.read_text(encoding="utf-8", errors="ignore")
    kind = doc_type or classify_document(filename, text)
    if row is None:
        row = Document(
            course_id=course.id,
            filename=filename,
            content_hash=digest,
        )
        session.add(row)
    row.content_hash = digest
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
    with session_scope(settings) as session:
        for raw_enrollment in enrollments:
            parsed_course = parse_enrollment_item(raw_enrollment)
            if not parsed_course:
                continue
            org_id = parsed_course["org_unit_id"]
            raw_dir = _raw_course_dir(settings, org_id)
            write_raw_json(raw_dir / "enrollment.json", raw_enrollment)
            course = upsert_course(session, parsed_course, term=term)

            news_items = news_by_org.get(org_id, [])
            news_path = write_raw_json(raw_dir / "news.json", news_items)
            for item in news_items:
                parsed = parse_news_item(item)
                upsert_announcement(session, course, parsed, str(news_path))
                for attachment in parsed["attachments"]:
                    dest = _resolve_attachment(attachment, attachment_files, raw_dir)
                    if dest:
                        upsert_document(session, course, dest, dest.name)

            dropbox_items = dropbox_by_org.get(org_id, [])
            dropbox_path = write_raw_json(raw_dir / "dropbox.json", dropbox_items)
            for item in dropbox_items:
                parsed = parse_dropbox_item(item)
                assignment = upsert_assignment(session, course, parsed, str(dropbox_path))
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

        session.flush()
        return snapshot_counts(session)


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


def parse_onq_api_response(status: int, content_type: str, text: str) -> Any:
    """Map an onQ HTTP response to JSON. Never includes body/cookies in the error."""
    ctype = content_type or ""
    if status == 404:
        raise FileNotFoundError("onQ 404")
    if status in {401, 403}:
        raise _session_expired_error(status, ctype)
    peek = (text or "")[:80].lstrip().lower()
    if "html" in ctype.lower() or peek.startswith("<!") or peek.startswith("<html"):
        raise _session_expired_error(status or 200, ctype or "text/html")
    if status >= 400:
        raise _session_expired_error(status, ctype)
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise IngestError(f"{SESSION_EXPIRED} (response was not JSON)") from exc


def _looks_like_html_bytes(content_type: str, payload: bytes) -> bool:
    ctype = (content_type or "").lower()
    head = payload[:64].lstrip().lower()
    return "html" in ctype or head.startswith(b"<!doctype") or head.startswith(b"<html")


class PlaywrightLEClient:
    """Brightspace LP/LE calls through Chromium's request context (cookies, UA, CSRF)."""

    def __init__(self, context, base_url: str) -> None:
        self.context = context
        self.base = base_url.rstrip("/")

    async def _csrf_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base}/d2l/home",
            "Origin": self.base,
        }
        cookies = await self.context.cookies()
        for cookie in cookies:
            if cookie.get("name") == "d2lSessionVal":
                value = cookie.get("value") or ""
                if value:
                    headers["X-Csrf-Token"] = value
                break
        return headers

    def _abs(self, path: str) -> str:
        return path if path.startswith("http") else self.base + path

    async def _get_json(self, path: str) -> Any:
        url = self._abs(path)
        response = await self.context.request.get(
            url,
            headers=await self._csrf_headers(),
            timeout=30_000,
        )
        ctype = response.headers.get("content-type", "")
        text = await response.text()
        return parse_onq_api_response(response.status, ctype, text)

    async def my_enrollments(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        bookmark = ""
        for _ in range(25):
            path = f"/d2l/api/lp/{LP}/enrollments/myenrollments/?orgUnitTypeId=3"
            if bookmark:
                path += f"&bookmark={bookmark}"
            payload = await self._get_json(path)
            if isinstance(payload, list):
                items.extend(payload)
                break
            batch = payload.get("Items") or payload.get("items") or []
            items.extend(batch)
            paging = payload.get("PagingInfo") or payload.get("pagingInfo") or {}
            more = paging.get("HasMoreItems")
            if more is None:
                more = paging.get("hasMoreItems")
            bookmark = str(paging.get("Bookmark") or paging.get("bookmark") or "")
            if not more or not bookmark:
                break
        return items

    async def news(self, org_unit_id: str) -> list[dict[str, Any]]:
        try:
            payload = await self._get_json(f"/d2l/api/le/{LE}/{org_unit_id}/news/")
        except FileNotFoundError:
            return []
        return payload if isinstance(payload, list) else payload.get("Items") or []

    async def dropbox_folders(self, org_unit_id: str) -> list[dict[str, Any]]:
        try:
            payload = await self._get_json(f"/d2l/api/le/{LE}/{org_unit_id}/dropbox/folders/")
        except FileNotFoundError:
            return []
        return payload if isinstance(payload, list) else payload.get("Items") or []

    async def download_file(self, url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = await self.context.request.get(
            self._abs(url),
            headers=await self._csrf_headers(),
            timeout=60_000,
        )
        if response.status >= 400:
            return False
        payload = await response.body()
        ctype = response.headers.get("content-type", "")
        if _looks_like_html_bytes(ctype, payload):
            return False
        dest.write_bytes(payload)
        return True


def ingest_live(settings: Settings | None = None) -> dict[str, int]:
    """Headless Chromium: LP/LE APIs + attachment download via the login session."""
    import asyncio

    return asyncio.run(_ingest_live_async(settings or get_settings()))


async def _ingest_live_async(settings: Settings) -> dict[str, int]:
    from school_secretary.ingest.browser import _headless_context, download_with_dom, scrape_pdf_links
    from school_secretary.ingest.parser import (
        extract_attachments_from_html,
        extract_attachments_from_json,
        merge_attachments,
    )

    if not settings.session_path.exists():
        raise IngestError(
            f"Missing {settings.storage_state_path}. "
            "Run `uv run school-secretary login` first (NetID, password, Duo). "
            "Headless refresh reuses that JSON so MFA is not prompted every time."
        )

    playwright = None
    context = None
    try:
        try:
            playwright, context = await _headless_context(settings)
        except FileNotFoundError as exc:
            raise IngestError(str(exc)) from None
        page = context.pages[0] if context.pages else await context.new_page()
        home = settings.onq_base_url.rstrip("/") + "/d2l/home"
        try:
            await page.goto(home, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise IngestError(
                f"{SESSION_EXPIRED} (headless could not open onQ home)"
            ) from exc
        landed = (page.url or "").lower()
        if any(part in landed for part in ("login", "adfs", "microsoftonline")):
            raise IngestError(f"{SESSION_EXPIRED} (headless landed on a login page)")

        client = PlaywrightLEClient(context, settings.onq_base_url)
        enrollments = await client.my_enrollments()
        news_by_org: dict[str, list[dict[str, Any]]] = {}
        dropbox_by_org: dict[str, list[dict[str, Any]]] = {}
        attachment_files: dict[str, Path] = {}
        parsed_courses = [p for e in enrollments if (p := parse_enrollment_item(e))]

        for parsed in parsed_courses:
            org_id = parsed["org_unit_id"]
            news_by_org[org_id] = await client.news(org_id)
            dropbox_by_org[org_id] = await client.dropbox_folders(org_id)
            raw_dir = _raw_course_dir(settings, org_id)
            for kind, collection in (
                ("news", news_by_org[org_id]),
                ("dropbox", dropbox_by_org[org_id]),
            ):
                for item in collection:
                    html = ""
                    body = item.get("Body") or item.get("Instructions") or {}
                    if isinstance(body, dict):
                        html = body.get("Html") or ""
                    parent_id = str(item.get("Id") or item.get("id") or "")
                    for attachment in merge_attachments(
                        extract_attachments_from_json(item),
                        extract_attachments_from_html(html),
                    ):
                        filename = attachment.get("filename") or f"{attachment.get('file_id')}.bin"
                        dest = raw_dir / filename
                        url = attachment.get("url") or ""
                        file_id = attachment.get("file_id") or ""
                        if not url and file_id and parent_id:
                            url = attachment_api_path(kind, org_id, parent_id, file_id)
                        if url and await client.download_file(url, dest):
                            attachment_files[filename] = dest

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
                if "?" in filename:
                    filename = filename.split("?")[0]
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"
                dest = raw_dir / filename
                if dest.exists():
                    attachment_files[filename] = dest
                    continue
                if await client.download_file(link, dest):
                    attachment_files[filename] = dest
                    continue
                try:
                    await download_with_dom(page, link, dest)
                    attachment_files[filename] = dest
                except Exception:
                    continue

        return ingest_payloads(settings, enrollments, news_by_org, dropbox_by_org, attachment_files)
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
