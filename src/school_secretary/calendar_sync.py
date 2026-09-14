from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from school_secretary.config import Settings, get_settings
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
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_refresh_token
    )


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
) -> CalendarEvent:
    row = (
        session.query(CalendarEvent)
        .filter_by(source_kind=kind, source_id=source_id)
        .one_or_none()
    )
    start = _aware(when, settings)
    end = start + timedelta(minutes=30)
    if row is None:
        row = CalendarEvent(
            source_kind=kind,
            source_id=source_id,
            uid=f"ss-{kind}-{source_id}-{uuid4().hex[:8]}@school-secretary.local",
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
        for assignment in session.query(Assignment).filter(Assignment.due_at.is_not(None)).all():
            title = f"{assignment.course.code}: {assignment.title}"
            events.append(
                _upsert_event(
                    session,
                    kind="assignment",
                    source_id=assignment.id,
                    title=title,
                    when=assignment.due_at,
                    settings=settings,
                )
            )
        for task in session.query(SubTask).filter(SubTask.due_at.is_not(None)).all():
            parent = task.assignment
            title = f"{parent.course.code} step: {task.title}"
            events.append(
                _upsert_event(
                    session,
                    kind="subtask",
                    source_id=task.id,
                    title=title,
                    when=task.due_at,
                    settings=settings,
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
            except Exception as exc:
                error = str(exc)
                transport = "ics"
                for event in events:
                    event.transport = "ics"
                    event.last_error = error[:500]
                    event.synced_at = now
        else:
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
