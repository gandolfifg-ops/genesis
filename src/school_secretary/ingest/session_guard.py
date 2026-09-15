from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pathlib import Path

from school_secretary.agents.persona import polish_outgoing
from school_secretary.config import Settings, get_settings

STALE_HOURS = 48
ALERT_COOLDOWN_HOURS = 12
EXPIRED_MARKERS = (
    "session expired",
    "not authorized",
    "landed on a login page",
    "duo",
    "adfs",
    "microsoftonline",
    "http 401",
    "http 403",
)

SESSION_WARNING = """\
**onQ session needs a refresh**

{reason}

On your own machine (WSL or desktop, headed Chromium + Duo):

`uv run school-secretary login`

or

`uv run school-secretary ingest --live`

A cloud VPS cannot complete Queen's Duo. I do not print cookies.
"""


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def health_path(settings: Settings) -> Path:
    return settings.data_dir / "session_health.json"


def _load(settings: Settings) -> dict:
    path = settings.data_dir / "session_health.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save(settings: Settings, payload: dict) -> None:
    settings.ensure_dirs()
    path = settings.data_dir / "session_health.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_expired_session_error(message: str) -> bool:
    blob = (message or "").lower()
    return any(marker in blob for marker in EXPIRED_MARKERS)


def record_ingest_success(settings: Settings, *, live: bool) -> None:
    now = datetime.now(tz=_tz(settings)).isoformat()
    data = _load(settings)
    data["last_ok_any_at"] = now
    if live:
        data["last_ok_live_at"] = now
        data["last_error"] = ""
    _save(settings, data)


def record_ingest_failure(settings: Settings, message: str) -> None:
    data = _load(settings)
    # Never persist cookie headers or raw bodies — callers must pass a safe message.
    text = " ".join((message or "").split())[:400]
    if "cookie" in text.lower() or "set-cookie" in text.lower():
        text = "onQ session expired or not authorized."
    data["last_error"] = text
    data["last_error_at"] = datetime.now(tz=_tz(settings)).isoformat()
    _save(settings, data)


def _parse_iso(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt


def inspect_session(settings: Settings | None = None, now: datetime | None = None) -> dict[str, str | bool]:
    """Cookie-file age and last live ingest. Never reads cookie values."""
    settings = settings or get_settings()
    tz = _tz(settings)
    now = now or datetime.now(tz=tz)
    data = _load(settings)
    last_live = _parse_iso(str(data.get("last_ok_live_at") or "") or None, tz)
    last_error = str(data.get("last_error") or "")
    session_file = settings.session_path
    age_hours: float | None = None
    if session_file.exists():
        mtime = datetime.fromtimestamp(session_file.stat().st_mtime, tz=tz)
        age_hours = (now - mtime).total_seconds() / 3600

    status = "ok"
    reason = "onQ session looks current."
    if last_error and is_expired_session_error(last_error):
        status = "expired"
        reason = "Brightspace rejected the saved session (login, Duo, or HTTP 401/403)."
    elif last_live is not None and now - last_live > timedelta(hours=STALE_HOURS):
        status = "stale"
        reason = f"Course data has not been live-ingested in over {STALE_HOURS} hours."
    elif not session_file.exists() and last_live is not None:
        status = "missing"
        reason = "The local session file is gone. Re-run headed login on WSL."
    elif age_hours is not None and age_hours > STALE_HOURS:
        status = "stale"
        reason = f"The saved onQ session is {int(age_hours)} hours old (limit {STALE_HOURS}h)."

    return {
        "status": status,
        "reason": reason,
        "should_alert": status != "ok",
        "has_session_file": session_file.exists(),
    }


def format_session_warning(reason: str) -> str:
    return polish_outgoing(SESSION_WARNING.format(reason=reason.strip()))


def maybe_alert_session(settings: Settings | None = None, now: datetime | None = None) -> str | None:
    """If expired/stale, return warning text and (when Telegram is configured) send it."""
    settings = settings or get_settings()
    tz = _tz(settings)
    now = now or datetime.now(tz=tz)
    report = inspect_session(settings, now=now)
    if not report["should_alert"]:
        return None
    data = _load(settings)
    last_alert = _parse_iso(str(data.get("last_alert_at") or "") or None, tz)
    if last_alert and now - last_alert < timedelta(hours=ALERT_COOLDOWN_HOURS):
        return None
    text = format_session_warning(str(report["reason"]))
    data["last_alert_at"] = now.isoformat()
    _save(settings, data)
    from school_secretary.telegram_app.notify import send_telegram_text

    send_telegram_text(settings, text)
    return text
