from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    extract_due_from_text,
    is_clutter_material,
    is_noisy_assignment_title,
    is_target_announcement,
    is_target_assignment,
    is_target_content_topic,
    is_target_material,
    parse_dropbox_item,
    parse_enrollment_item,
    parse_news_item,
)
from school_secretary.rag.extract import extract_file_text, file_hash

LE = "1.47"


class IngestError(RuntimeError):
    pass


SESSION_EXPIRED = (
    "onQ session expired or not authorized. "
    "Re-run `uv run school-secretary login` on WSL (NetID, password, Duo). "
    "Live ingest must run headed (`ingest --live`); do not pass --headless after a headed login."
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
    text = extract_file_text(path)
    if not text and path.suffix.lower() not in {".pdf", ".docx"}:
        text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    kind = doc_type or classify_document(filename, text)
    if kind == "clutter" and assignment is None:
        return None
    if assignment is None and kind == "handout" and not is_target_material(
        filename, text, filename, has_due=extract_due_from_text(text) is not None
    ):
        return None
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
                if not is_target_announcement(parsed.get("title"), parsed.get("body") or ""):
                    continue
                upsert_announcement(session, course, parsed, str(news_path))
                for attachment in parsed["attachments"]:
                    dest = _resolve_attachment(attachment, attachment_files, raw_dir)
                    if dest:
                        upsert_document(session, course, dest, dest.name)

            dropbox_items = dropbox_by_org.get(org_id, [])
            dropbox_path = write_raw_json(raw_dir / "dropbox.json", dropbox_items)
            for item in dropbox_items:
                parsed = parse_dropbox_item(item)
                if is_noisy_assignment_title(parsed.get("title")):
                    continue
                if not is_target_assignment(parsed.get("title"), parsed.get("instructions") or "", parsed.get("due_at")):
                    continue
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

        _attach_loose_raw_files(session, settings)
        session.flush()
        return snapshot_counts(session)


FILE_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".pptx", ".xlsx", ".rtf", ".md"}


def _attach_loose_raw_files(session, settings: Settings) -> None:
    for course in session.query(Course).all():
        raw_dir = settings.raw_dir / course.org_unit_id
        if not raw_dir.is_dir():
            continue
        assignments = list(course.assignments)
        for path in sorted(raw_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in FILE_SUFFIXES:
                continue
            stem = path.stem.lower().replace("_", " ").replace("-", " ")
            compact_stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
            linked = None
            for assignment in assignments:
                hay = (assignment.title or "").lower()
                compact_hay = re.sub(r"[^a-z0-9]+", "", hay)
                if (
                    stem in hay
                    or hay in stem
                    or path.stem.lower() in hay.replace(" ", "_")
                    or (compact_stem and compact_stem in compact_hay)
                    or (compact_hay and compact_hay in compact_stem)
                ):
                    linked = assignment
                    break
            if is_clutter_material(path.name, filename=path.name) and linked is None:
                continue
            upsert_document(session, course, path, path.name, assignment=linked)


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
    attachment_files: dict[str, Path] = {}
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
        for path in course_dir.iterdir():
            if path.is_file() and path.suffix.lower() in FILE_SUFFIXES:
                attachment_files[path.name] = path
    return ingest_payloads(settings, enrollments, news_by_org, dropbox_by_org, attachment_files)


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


_HOME_ORG_RE = re.compile(r"/d2l/home/(\d+)")
_API_LE_ORG_RE = re.compile(r"/d2l/api/le/[^/]+/(\d+)/")
_LE_NEWS_ORG_RE = re.compile(r"/d2l/le/news/(\d+)")
_LE_DROPBOX_ORG_RE = re.compile(r"/d2l/le/dropbox/(\d+)")
_LE_ORG_RE = re.compile(r"/d2l/le/(\d+)/")
_DB_ID_RE = re.compile(r"[?&]db=(\d+)")
_NEWS_ID_RE = re.compile(r"(?:newsid|newsId|NewsId)=(\d+)")
_SKIP_TITLES = {
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
    "folder",
    "due date",
}


def classify_onq_json_url(url: str) -> str | None:
    """Classify a natural onQ XHR URL. Path/query only — never logs the URL."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    blob = f"{path}?{parsed.query.lower()}"
    if "myenrollments" in blob:
        return "enrollments"
    if "/enrollments/" in path and "orgunit" in blob:
        return "enrollments"
    if "/news" in path:
        return "news"
    if "dropbox" in blob:
        return "dropbox"
    return None


def classify_onq_json_payload(payload: Any) -> str | None:
    items = _json_items(payload)
    if not items and isinstance(payload, dict) and (payload.get("OrgUnit") or payload.get("orgUnit")):
        return "enrollments"
    if not items:
        return None
    sample = items[0] if isinstance(items[0], dict) else {}
    if not isinstance(sample, dict):
        return None
    if sample.get("OrgUnit") or sample.get("orgUnit"):
        return "enrollments"
    if "DueDate" in sample or "dueDate" in sample or "Instructions" in sample or "instructions" in sample:
        return "dropbox"
    if ("Title" in sample or "title" in sample) and (
        "Body" in sample or "body" in sample or "StartDate" in sample or "startDate" in sample
    ):
        return "news"
    return None


def org_id_from_onq_url(url: str) -> str | None:
    parsed = urlparse(url)
    path = parsed.path
    for pattern in (_HOME_ORG_RE, _API_LE_ORG_RE, _LE_NEWS_ORG_RE, _LE_DROPBOX_ORG_RE, _LE_ORG_RE):
        match = pattern.search(path)
        if match:
            return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("ou", "orgUnitId", "orgUnit"):
        values = query.get(key) or []
        if values:
            return values[0]
    return None


def enrollment_from_tile(org_id: str, label: str) -> dict[str, Any]:
    text = " ".join((label or "").split())
    return {
        "OrgUnit": {
            "Id": int(org_id) if org_id.isdigit() else org_id,
            "Name": text or f"Course {org_id}",
            "Code": text or org_id,
            "Type": {"Id": 3, "Code": "Course Offering"},
        }
    }


def news_from_dom(title: str, body: str, d2l_id: str | None = None) -> dict[str, Any]:
    heading = (title or "Announcement").strip() or "Announcement"
    text = (body or "").strip()
    return {
        "Id": d2l_id or _stable_id(heading, text),
        "Title": heading,
        "Body": {"Text": text or heading, "Html": text or heading},
    }


def dropbox_from_dom(
    title: str,
    due: str | None = None,
    instructions: str = "",
    d2l_id: str | None = None,
    href: str | None = None,
) -> dict[str, Any]:
    name = (title or "Assignment").strip() or "Assignment"
    item: dict[str, Any] = {
        "Id": d2l_id or _stable_id(name, due or "", instructions),
        "Name": name,
        "Instructions": {"Text": (instructions or "").strip()},
    }
    if due:
        item["DueDate"] = due
    if href:
        item["Url"] = href
    return item


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _json_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get("Items") or payload.get("items")
        if isinstance(items, list):
            return items
    return []


def _merge_enrollment_items(existing: list[dict[str, Any]], incoming: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in existing + [x for x in incoming if isinstance(x, dict)]:
        parsed = parse_enrollment_item(item)
        if not parsed:
            continue
        by_id[parsed["org_unit_id"]] = item
    return list(by_id.values())


def _merge_named_items(existing: list[dict[str, Any]], incoming: list[Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in existing + [x for x in incoming if isinstance(x, dict)]:
        key = str(item.get("Id") or item.get("id") or item.get("Name") or item.get("Title") or "")
        if not key:
            key = _stable_id(json.dumps(item, sort_keys=True, default=str))
        by_id[key] = item
    return list(by_id.values())


def _is_api_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/d2l/api/le/" in path or "/d2l/api/lp/" in path


class OnqXhrCapture:
    """Collect enrollments/news/dropbox JSON from natural page XHR. Never logs bodies."""

    def __init__(self) -> None:
        self.enrollments: list[dict[str, Any]] = []
        self.news_by_org: dict[str, list[dict[str, Any]]] = {}
        self.dropbox_by_org: dict[str, list[dict[str, Any]]] = {}
        self.current_org: str | None = None

    def set_org(self, org_id: str | None) -> None:
        self.current_org = org_id

    async def ingest_response(self, response: Any) -> None:
        try:
            url = response.url or ""
            if response.status != 200:
                return
            kind = classify_onq_json_url(url)
            payload = await _safe_response_json(response)
            if payload is None:
                return
            if kind is None:
                kind = classify_onq_json_payload(payload)
            if kind is None:
                return
            items = _json_items(payload)
            if kind == "enrollments":
                if not items and isinstance(payload, dict) and (payload.get("OrgUnit") or payload.get("orgUnit")):
                    items = [payload]
                self.enrollments = _merge_enrollment_items(self.enrollments, items)
                return
            org_id = org_id_from_onq_url(url) or self.current_org
            if not org_id:
                return
            bucket = self.news_by_org if kind == "news" else self.dropbox_by_org
            bucket[org_id] = _merge_named_items(bucket.get(org_id, []), items)
        except Exception:
            return


async def _safe_response_json(response: Any) -> Any | None:
    try:
        ctype = (response.headers.get("content-type") or "").lower()
        url = (response.url or "").lower()
        looks_json = "json" in ctype or "javascript" in ctype or "/d2l/api/" in urlparse(url).path
        if not looks_json:
            return None
        return await response.json()
    except Exception:
        try:
            text = await response.text()
        except Exception:
            return None
        snippet = (text or "").lstrip()[:1]
        if snippet not in {"{", "["}:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None


async def _goto_html(page, url: str, *, wait: str = "domcontentloaded") -> bool:
    try:
        await page.goto(url, wait_until=wait, timeout=90_000)
    except Exception:
        landed = (page.url or "").lower()
        if "/d2l/" not in landed:
            return False
    landed = (page.url or "").lower()
    if any(part in landed for part in ("login", "adfs", "microsoftonline")):
        return False
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass
    await page.wait_for_timeout(400)
    return True


async def _click_view_all_courses(page) -> None:
    loc = page.locator(
        "d2l-button:has-text('View All'), button:has-text('View All'), "
        "a:has-text('View All Courses'), a:has-text('View All')"
    )
    try:
        if await loc.count():
            await loc.first.click(timeout=5_000)
            await page.wait_for_timeout(800)
    except Exception:
        return


async def _locator_count_text(loc, limit: int = 80) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    try:
        n = await loc.count()
    except Exception:
        return found
    for i in range(min(n, limit)):
        el = loc.nth(i)
        href = ""
        text = ""
        try:
            href = (await el.get_attribute("href")) or ""
        except Exception:
            href = ""
        try:
            text = " ".join(((await el.inner_text()) or "").split())
        except Exception:
            try:
                text = ((await el.get_attribute("title")) or "").strip()
            except Exception:
                text = ""
        found.append((href, text))
    return found


async def scrape_course_tiles(page) -> list[dict[str, Any]]:
    """Course tiles/links from the rendered homepage, including iframes and shadow DOM."""
    by_id: dict[str, dict[str, Any]] = {}
    frames = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    if page not in frames:
        frames = [page, *frames]
    for frame in frames:
        try:
            cards = frame.locator("d2l-enrollment-card")
            n = await cards.count()
        except Exception:
            n = 0
        for i in range(min(n, 80)):
            card = cards.nth(i)
            href = ""
            label = ""
            try:
                href = (await card.get_attribute("href")) or ""
            except Exception:
                href = ""
            try:
                inner = card.locator("a[href*='/d2l/home/']").first
                if await inner.count():
                    href = href or ((await inner.get_attribute("href")) or "")
            except Exception:
                pass
            try:
                label = " ".join(((await card.inner_text()) or "").split())
            except Exception:
                label = ""
            org_id = org_id_from_onq_url(href)
            if org_id:
                by_id[org_id] = enrollment_from_tile(org_id, label)
        try:
            links = await _locator_count_text(frame.locator("a[href*='/d2l/home/']"))
        except Exception:
            links = []
        for href, text in links:
            org_id = org_id_from_onq_url(href)
            if not org_id:
                continue
            if text.lower() in _SKIP_TITLES:
                continue
            existing = by_id.get(org_id)
            if existing is None or len(text) > len(str(existing.get("OrgUnit", {}).get("Name") or "")):
                by_id[org_id] = enrollment_from_tile(org_id, text)
    return list(by_id.values())


async def scrape_announcements_dom(page, org_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    roots = page.locator(
        ".d2l-widget:has-text('Announcement'), "
        "d2l-card:has-text('Announcement'), "
        "d2l-news-widget, "
        "[data-widget-name*='news' i]"
    )
    blocks = roots.locator("d2l-html-block, li, article, .d2l-datalist-item, d2l-collapsible-panel")
    try:
        n = await blocks.count()
    except Exception:
        n = 0
    if n == 0:
        blocks = page.locator("d2l-news-item, li.d2l-datalist-item, .d2l-datalist-simpleitem")
        try:
            n = await blocks.count()
        except Exception:
            n = 0
    for i in range(min(n, 40)):
        try:
            text = "\n".join(
                line.strip() for line in ((await blocks.nth(i).inner_text()) or "").splitlines() if line.strip()
            )
        except Exception:
            continue
        if not text or text.lower() in _SKIP_TITLES:
            continue
        lines = text.splitlines()
        title = lines[0][:200]
        body = "\n".join(lines[1:]) or title
        news_id = None
        try:
            href = await blocks.nth(i).locator("a[href]").first.get_attribute("href")
            if href:
                match = _NEWS_ID_RE.search(href)
                if match:
                    news_id = match.group(1)
        except Exception:
            news_id = None
        items.append(news_from_dom(title, body, news_id or _stable_id(org_id, title, body)))
    links = await _locator_count_text(page.locator("a[href*='newsid='], a[href*='NewsId=']"))
    for href, text in links:
        if not text or text.lower() in _SKIP_TITLES:
            continue
        match = _NEWS_ID_RE.search(href)
        items.append(news_from_dom(text, text, match.group(1) if match else _stable_id(org_id, text)))
    return _merge_named_items([], items)


async def scrape_assignments_dom(page, org_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    rows = page.locator("table tr, d2l-table tr, tbody tr, d2l-list-item")
    try:
        n = await rows.count()
    except Exception:
        n = 0
    for i in range(min(n, 80)):
        row = rows.nth(i)
        try:
            links = row.locator("a[href]")
            count = await links.count()
        except Exception:
            continue
        title = ""
        href = ""
        for j in range(count):
            try:
                candidate = " ".join(((await links.nth(j).inner_text()) or "").split())
                candidate_href = (await links.nth(j).get_attribute("href")) or ""
            except Exception:
                continue
            if is_noisy_assignment_title(candidate):
                continue
            title, href = candidate, candidate_href
            if "db=" in candidate_href or "dropbox" in candidate_href.lower():
                break
        if not title or is_noisy_assignment_title(title):
            continue
        if _HOME_ORG_RE.search(href) and "dropbox" not in href.lower() and "db=" not in href.lower():
            continue
        try:
            row_text = " ".join(((await row.inner_text()) or "").split())
        except Exception:
            row_text = title
        due_dt = extract_due_from_text(row_text)
        due = due_dt.isoformat() if due_dt else None
        if not is_target_assignment(title, row_text, due_dt):
            continue
        db_match = _DB_ID_RE.search(href)
        d2l_id = db_match.group(1) if db_match else _stable_id(org_id, title)
        instructions = row_text.replace(title, "", 1).strip()
        items.append(dropbox_from_dom(title, due, instructions, d2l_id, href=href))
    extra = await _locator_count_text(
        page.locator("a[href*='db='], a[href*='dropbox'], a[href*='/assignments/']")
    )
    for href, text in extra:
        if is_noisy_assignment_title(text):
            continue
        if not is_target_assignment(text, text, None):
            continue
        db_match = _DB_ID_RE.search(href)
        items.append(dropbox_from_dom(text, None, "", db_match.group(1) if db_match else _stable_id(org_id, text), href=href))
    return _merge_named_items([], items)


def _abs_url(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return base.rstrip("/") + "/" + href


async def _expand_content_modules(page) -> None:
    loc = page.locator(
        "button[aria-expanded='false'], d2l-button-icon[icon*='expand'], "
        "d2l-expand-collapse-content, .d2l-expandcollapse-toggle"
    )
    try:
        n = await loc.count()
    except Exception:
        return
    for i in range(min(n, 25)):
        try:
            await loc.nth(i).click(timeout=2_000)
            await page.wait_for_timeout(200)
        except Exception:
            continue


async def _download_page_files(page, dest_dir: Path, limit: int = 20, *, strict: bool = False) -> dict[str, Path]:
    from school_secretary.ingest.browser import download_page_files

    try:
        return await download_page_files(page, dest_dir, limit=limit, strict=strict)
    except Exception:
        return {}


async def _heading_text(page) -> str:
    for selector in ("h1", "d2l-heading", ".d2l-page-title", ".d2l-page-header", "h2"):
        loc = page.locator(selector)
        try:
            if await loc.count():
                text = " ".join(((await loc.first.inner_text()) or "").split())
                if text and not is_noisy_assignment_title(text):
                    return text[:200]
        except Exception:
            continue
    return ""


async def _visible_body_text(page) -> str:
    loc = page.locator("d2l-html-block, .d2l-htmlblock, .d2l-body, d2l-collapsible-panel")
    chunks: list[str] = []
    try:
        n = await loc.count()
    except Exception:
        n = 0
    for i in range(min(n, 12)):
        try:
            text = (await loc.nth(i).inner_text() or "").strip()
        except Exception:
            continue
        if text:
            chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    try:
        return (await page.locator("main, body").first.inner_text())[:8000]
    except Exception:
        return ""


async def scrape_assignment_detail(page, org_id: str, href: str, base: str) -> dict[str, Any] | None:
    url = _abs_url(base, href)
    if not url or _is_api_url(url):
        return None
    if not await _goto_html(page, url):
        return None
    title = await _heading_text(page)
    body = await _visible_body_text(page)
    blob = f"{title}\n{body}"
    due_dt = extract_due_from_text(blob)
    if is_noisy_assignment_title(title):
        title = ""
    if not title:
        return None
    if not is_target_assignment(title, body, due_dt):
        return None
    db_match = _DB_ID_RE.search(href) or _DB_ID_RE.search(page.url or "")
    d2l_id = db_match.group(1) if db_match else _stable_id(org_id, title)
    return dropbox_from_dom(
        title,
        due_dt.isoformat() if due_dt else None,
        body,
        d2l_id,
        href=url,
    )


async def crawl_content_modules(page, org_id: str, base: str, dest_dir: Path) -> dict[str, Path]:
    saved: dict[str, Path] = {}
    urls = [
        f"{base}/d2l/le/content/{org_id}/Home",
        f"{base}/d2l/le/lessons/{org_id}",
        f"{base}/d2l/lms/content/home.d2l?ou={org_id}",
    ]
    topic_hrefs: list[str] = []
    for url in urls:
        if not await _goto_html(page, url):
            continue
        await _expand_content_modules(page)
        saved.update(await _download_page_files(page, dest_dir, strict=True))
        links = await _locator_count_text(
            page.locator(
                "a[href*='viewContent'], a[href*='/topics/'], a[href*='content/viewer'], "
                "a[href*='/le/content/']"
            ),
            limit=80,
        )
        for href, text in links:
            if not href or _is_api_url(href):
                continue
            if text.lower() in _SKIP_TITLES:
                continue
            if not is_target_content_topic(text):
                continue
            abs_url = _abs_url(base, href)
            if org_id not in abs_url and f"ou={org_id}" not in abs_url:
                continue
            topic_hrefs.append(abs_url)
    seen: set[str] = set()
    unique: list[str] = []
    for href in topic_hrefs:
        if href in seen:
            continue
        seen.add(href)
        unique.append(href)
    for href in unique[:20]:
        if not await _goto_html(page, href):
            continue
        saved.update(await _download_page_files(page, dest_dir, strict=True))
    return saved


async def crawl_announcement_pages(
    page, org_id: str, base: str, dest_dir: Path, existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    saved: dict[str, Path] = {}
    items = list(existing)
    await _goto_html(page, f"{base}/d2l/lms/news/main.d2l?ou={org_id}")
    items = _merge_named_items(items, await scrape_announcements_dom(page, org_id))
    saved.update(await _download_page_files(page, dest_dir))
    links = await _locator_count_text(
        page.locator("a[href*='newsid='], a[href*='NewsId='], a[href*='view.d2l']"),
        limit=30,
    )
    for href, text in links[:20]:
        url = _abs_url(base, href)
        if not url or _is_api_url(url):
            continue
        if not await _goto_html(page, url):
            continue
        heading = await _heading_text(page) or text
        body = await _visible_body_text(page)
        match = _NEWS_ID_RE.search(href)
        items.append(news_from_dom(heading, body or heading, match.group(1) if match else None))
        saved.update(await _download_page_files(page, dest_dir, limit=10, strict=True))
    kept = []
    for item in _merge_named_items([], items):
        title = str(item.get("Title") or "")
        body_payload = item.get("Body") or ""
        body = body_payload.get("Text") if isinstance(body_payload, dict) else str(body_payload)
        if is_target_announcement(title, body or ""):
            kept.append(item)
    return kept, saved


async def crawl_dropbox_pages(
    page, org_id: str, base: str, dest_dir: Path, existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    saved: dict[str, Path] = {}
    collected: list[dict[str, Any]] = list(existing)
    list_urls = [
        f"{base}/d2l/lms/dropbox/user/folders_list.d2l?ou={org_id}",
        f"{base}/d2l/le/{org_id}/assignments/list",
        f"{base}/d2l/le/dropbox/{org_id}/List",
    ]
    hrefs: list[tuple[str, str]] = []
    for url in list_urls:
        if not await _goto_html(page, url):
            continue
        collected = _merge_named_items(collected, await scrape_assignments_dom(page, org_id))
        saved.update(await _download_page_files(page, dest_dir, strict=True))
        for item in collected:
            href = item.get("Url") or ""
            name = item.get("Name") or ""
            instr = item.get("Instructions") or ""
            if isinstance(instr, dict):
                instr = instr.get("Text") or ""
            if href and is_target_assignment(name, str(instr), item.get("DueDate")):
                hrefs.append((_abs_url(base, href), name))
    seen: set[str] = set()
    for href, _name in hrefs:
        if not href or href in seen or _is_api_url(href):
            continue
        seen.add(href)
        detail = await scrape_assignment_detail(page, org_id, href, base)
        if detail:
            collected = _merge_named_items(collected, [detail])
        saved.update(await _download_page_files(page, dest_dir, limit=12))
        if len(seen) >= 30:
            break
    cleaned = []
    for item in collected:
        name = str(item.get("Name") or "")
        instr = item.get("Instructions") or ""
        if isinstance(instr, dict):
            instr = instr.get("Text") or ""
        if is_target_assignment(name, str(instr), item.get("DueDate")):
            cleaned.append(item)
    return cleaned, saved


def complete_live_ingest(
    settings: Settings,
    enrollments: list[dict[str, Any]],
    news_by_org: dict[str, list[dict[str, Any]]],
    dropbox_by_org: dict[str, list[dict[str, Any]]],
    attachment_files: dict[str, Path],
) -> dict[str, int]:
    """SQLite upsert + parse downloaded files + Chroma embed + replan. No extra CLI."""
    from school_secretary.agents.planner import replan_all
    from school_secretary.calendar_sync import sync_calendar
    from school_secretary.db.session import init_db
    from school_secretary.rag.index import index_documents

    init_db(settings)
    counts = ingest_payloads(settings, enrollments, news_by_org, dropbox_by_org, attachment_files)
    indexed = index_documents(settings, force=True)
    with session_scope(settings) as session:
        planned = replan_all(session)
    calendar = sync_calendar(settings)
    counts["indexed"] = indexed
    counts["planned"] = planned
    counts["calendar_events"] = int(calendar.get("events") or 0)
    return counts


def ingest_raw(settings: Settings | None = None) -> dict[str, int]:
    """Parse `data/raw/` JSON + downloaded files into SQLite and embed (no browser)."""
    settings = settings or get_settings()
    from school_secretary.agents.planner import replan_all
    from school_secretary.calendar_sync import sync_calendar
    from school_secretary.db.session import init_db
    from school_secretary.rag.index import index_documents

    init_db(settings)
    payloads = ingest_from_raw_dir(settings)

    indexed = index_documents(settings, force=True)
    with session_scope(settings) as session:
        planned = replan_all(session)
    calendar = sync_calendar(settings)
    payloads["indexed"] = indexed
    payloads["planned"] = planned
    payloads["calendar_events"] = int(calendar.get("events") or 0)
    return payloads


async def ingest_live_from_context(settings: Settings, context, page) -> dict[str, int]:
    """Populate SQLite from the open headed onQ tab. Used by login and ingest --live.

    Walks content modules, announcements, and dropboxes; clicks downloads into
    data/raw/; then parses PDFs and embeds into Chroma before returning.
    """
    base = settings.onq_base_url.rstrip("/")
    home = f"{base}/d2l/home"
    capture = OnqXhrCapture()

    async def _on_response(response: Any) -> None:
        await capture.ingest_response(response)

    page.on("response", _on_response)
    try:
        context.on("response", _on_response)
    except Exception:
        pass

    try:
        await page.goto(home, wait_until="networkidle", timeout=90_000)
    except Exception as exc:
        landed = (page.url or "").lower()
        if "/d2l/home" not in landed:
            raise IngestError(f"{SESSION_EXPIRED} (could not open onQ home)") from exc
    landed = (page.url or "").lower()
    if any(part in landed for part in ("login", "adfs", "microsoftonline")):
        raise IngestError(f"{SESSION_EXPIRED} (landed on a login page)")
    await _click_view_all_courses(page)
    try:
        await page.locator("d2l-enrollment-card, a[href*='/d2l/home/']").first.wait_for(timeout=15_000)
    except Exception:
        pass

    enrollments = list(capture.enrollments)
    if not enrollments:
        enrollments = await scrape_course_tiles(page)
    else:
        enrollments = _merge_enrollment_items(enrollments, await scrape_course_tiles(page))

    parsed_courses = [p for e in enrollments if (p := parse_enrollment_item(e))]
    if not parsed_courses:
        raise IngestError(
            "Could not find courses on the onQ homepage. "
            "Make sure course tiles are visible, then re-run `school-secretary login` or `ingest --live`."
        )

    news_by_org: dict[str, list[dict[str, Any]]] = dict(capture.news_by_org)
    dropbox_by_org: dict[str, list[dict[str, Any]]] = dict(capture.dropbox_by_org)
    attachment_files: dict[str, Path] = {}

    for parsed in parsed_courses:
        org_id = parsed["org_unit_id"]
        capture.set_org(org_id)
        raw_dir = _raw_course_dir(settings, org_id)
        course_home = f"{base}/d2l/home/{org_id}"
        await _goto_html(page, course_home)
        news_by_org[org_id] = _merge_named_items(
            news_by_org.get(org_id, []) + capture.news_by_org.get(org_id, []),
            await scrape_announcements_dom(page, org_id),
        )
        attachment_files.update(await _download_page_files(page, raw_dir))

        news_items, news_files = await crawl_announcement_pages(
            page, org_id, base, raw_dir, news_by_org[org_id]
        )
        news_by_org[org_id] = news_items
        attachment_files.update(news_files)
        attachment_files.update(await crawl_content_modules(page, org_id, base, raw_dir))

        drop_items, drop_files = await crawl_dropbox_pages(
            page,
            org_id,
            base,
            raw_dir,
            dropbox_by_org.get(org_id, []) + capture.dropbox_by_org.get(org_id, []),
        )
        dropbox_by_org[org_id] = drop_items
        attachment_files.update(drop_files)

    return complete_live_ingest(settings, enrollments, news_by_org, dropbox_by_org, attachment_files)


def ingest_live(settings: Settings | None = None, *, headed: bool = True) -> dict[str, int]:
    """Headed Chromium: crawl content/dropbox, download files, parse, and embed.

    Brightspace rejects scripted LE/LP fetches. This path clicks in the open tab.
    Default is headed (headless=False) on the persistent data/browser/ profile.
    """
    import asyncio

    return asyncio.run(_ingest_live_async(settings or get_settings(), headed=headed))


async def _ingest_live_async(settings: Settings, *, headed: bool = True) -> dict[str, int]:
    from school_secretary.ingest.browser import _open_persistent_context

    if not settings.session_path.exists():
        raise IngestError(
            f"Missing {settings.storage_state_path}. "
            "Run `uv run school-secretary login` first (NetID, password, Duo). "
            "Live ingest reuses data/browser/ in a headed window (headless=False)."
        )

    # headless=False by default: headed TLS/session cookies fail under headless Chromium.
    headless = False if headed else True
    playwright = None
    context = None
    try:
        try:
            playwright, context = await _open_persistent_context(settings, headless=headless)
        except FileNotFoundError as exc:
            raise IngestError(str(exc)) from None
        page = context.pages[0] if context.pages else await context.new_page()
        return await ingest_live_from_context(settings, context, page)
    finally:
        if context is not None:
            await context.close()
        if playwright is not None:
            await playwright.stop()
