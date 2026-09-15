from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from school_secretary.config import Settings, get_settings
from school_secretary.db.live import live_course_ids
from school_secretary.db.models import Assignment, CalendarEvent, SubTask
from school_secretary.db.session import init_db, session_scope

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _aware(dt: datetime, settings: Settings) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_tz(settings))
    return dt.astimezone(_tz(settings))


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _ics_dt(dt: datetime) -> str:
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%dT%H%M%SZ")


def google_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        (settings.google_oauth_client_id or "").strip()
        and (settings.google_oauth_client_secret or "").strip()
        and (settings.google_oauth_refresh_token or "").strip()
    )


def google_client_loaded(settings: Settings | None = None) -> bool:
    """True when client id+secret were loaded from .env (refresh token may still be missing)."""
    settings = settings or get_settings()
    return bool(
        (settings.google_oauth_client_id or "").strip()
        and (settings.google_oauth_client_secret or "").strip()
    )


def _event_duration(kind: str, assignment_type: str) -> timedelta:
    blob = f"{kind} {assignment_type}".lower()
    if any(word in blob for word in ("quiz", "exam", "midterm", "test")):
        return timedelta(hours=2)
    if "lab" in blob:
        return timedelta(hours=1, minutes=30)
    return timedelta(minutes=30)


def _event_title(code: str, title: str, assignment_type: str, kind: str) -> str:
    prefix = "Due"
    atype = (assignment_type or "").lower()
    if kind == "subtask":
        prefix = "Step"
    elif "quiz" in atype or "exam" in atype:
        prefix = "Test"
    elif "lab" in atype:
        prefix = "Lab"
    elif "essay" in atype:
        prefix = "Essay"
    elif "project" in (title or "").lower():
        prefix = "Project"
    return f"{prefix} · {code}: {title}"


def _access_token(settings: Settings) -> str:
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "refresh_token": settings.google_oauth_refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20.0,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Google token response had no access_token")
    return token


def _push_google(settings: Settings, event: CalendarEvent, token: str) -> str:
    payload = {
        "summary": event.title,
        "description": "My School Secretary deadline (push sync). Do your own assignment work.",
        "start": {
            "dateTime": event.start_at.isoformat(),
            "timeZone": settings.timezone,
        },
        "end": {
            "dateTime": event.end_at.isoformat(),
            "timeZone": settings.timezone,
        },
    }
    url = GOOGLE_EVENTS_URL.format(calendar_id=settings.google_calendar_id or "primary")
    headers = {"Authorization": f"Bearer {token}"}
    if event.google_event_id:
        response = httpx.patch(
            f"{url}/{event.google_event_id}",
            headers=headers,
            json=payload,
            timeout=20.0,
        )
    else:
        response = httpx.post(url, headers=headers, json=payload, timeout=20.0)
    response.raise_for_status()
    return str(response.json().get("id") or event.google_event_id or "")


def _upsert_event(
    session,
    *,
    kind: str,
    source_id: int,
    title: str,
    when: datetime,
    settings: Settings,
    assignment_type: str = "other",
) -> CalendarEvent:
    row = (
        session.query(CalendarEvent)
        .filter_by(source_kind=kind, source_id=source_id)
        .one_or_none()
    )
    start = _aware(when, settings)
    end = start + _event_duration(kind, assignment_type)
    if row is None:
        row = CalendarEvent(
            source_kind=kind,
            source_id=source_id,
            uid=f"ss-{kind}-{source_id}@school-secretary.local",
            title=title,
            start_at=start,
            end_at=end,
        )
        session.add(row)
    else:
        row.title = title
        row.start_at = start
        row.end_at = end
    return row


def _write_ics(settings: Settings, events: list[CalendarEvent]) -> str:
    settings.ensure_dirs()
    stamp = datetime.now(tz=timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//My School Secretary//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event.uid}",
                f"DTSTAMP:{_ics_dt(stamp)}",
                f"DTSTART:{_ics_dt(event.start_at)}",
                f"DTEND:{_ics_dt(event.end_at)}",
                f"SUMMARY:{_ics_escape(event.title)}",
                "DESCRIPTION:Pushed by My School Secretary. Scaffolding only — you write the work.",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    path = settings.calendar_dir / "school-secretary.ics"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return str(path)


def sync_calendar(settings: Settings | None = None) -> dict[str, str | int]:
    """Push assignment and sub-task deadlines to Google Calendar, or a local ICS file."""
    settings = settings or get_settings()
    init_db(settings)
    now = datetime.now(tz=_tz(settings))
    with session_scope(settings) as session:
        events: list[CalendarEvent] = []
        live_ids = live_course_ids(session)
        asg_q = session.query(Assignment).filter(Assignment.due_at.is_not(None))
        task_q = session.query(SubTask).filter(SubTask.due_at.is_not(None))
        if live_ids:
            asg_q = asg_q.filter(Assignment.course_id.in_(live_ids))
            task_q = task_q.join(Assignment).filter(Assignment.course_id.in_(live_ids))
        for assignment in asg_q.all():
            title = _event_title(
                assignment.course.code,
                assignment.title,
                assignment.assignment_type,
                "assignment",
            )
            events.append(
                _upsert_event(
                    session,
                    kind="assignment",
                    source_id=assignment.id,
                    title=title,
                    when=assignment.due_at,
                    settings=settings,
                    assignment_type=assignment.assignment_type,
                )
            )
        for task in task_q.all():
            parent = task.assignment
            title = _event_title(
                parent.course.code,
                task.title,
                parent.assignment_type,
                "subtask",
            )
            events.append(
                _upsert_event(
                    session,
                    kind="subtask",
                    source_id=task.id,
                    title=title,
                    when=task.due_at,
                    settings=settings,
                    assignment_type=parent.assignment_type,
                )
            )
        session.flush()
        transport = "ics"
        error = ""
        if google_configured(settings):
            try:
                token = _access_token(settings)
                for event in events:
                    event.google_event_id = _push_google(settings, event, token)
                    event.transport = "google"
                    event.last_error = ""
                    event.synced_at = now
                transport = "google"
            except Exception:
                error = "Google Calendar push failed; wrote a local calendar file instead."
                transport = "ics"
                for event in events:
                    event.transport = "ics"
                    event.last_error = error
                    event.synced_at = now
        else:
            if google_client_loaded(settings):
                error = "Google OAuth client is set; add GOOGLE_OAUTH_REFRESH_TOKEN to auto-push."
            for event in events:
                event.transport = "ics"
                event.last_error = ""
                event.synced_at = now
        ics_path = _write_ics(settings, events)
        return {
            "events": len(events),
            "transport": transport,
            "ics_path": ics_path,
            "google": int(google_configured(settings)),
            "error": error,
        }
