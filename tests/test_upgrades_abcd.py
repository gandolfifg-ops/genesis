from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from school_secretary.calendar_sync import google_client_loaded, google_configured, sync_calendar
from school_secretary.db.models import Assignment, Course, SubTask
from school_secretary.db.session import init_db, session_scope
from school_secretary.ingest.session_guard import (
    format_session_warning,
    inspect_session,
    maybe_alert_session,
    record_ingest_failure,
    record_ingest_success,
)


def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOOL_SECRETARY_SESSION_PATH", str(tmp_path / "storage_state.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "")
    monkeypatch.setenv("WHATSAPP_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    return get_settings()


def test_session_expired_error_sets_expired_status(tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    record_ingest_failure(settings, "Duo MFA / landed on a login page (HTTP 401)")
    report = inspect_session(settings)
    assert report["status"] == "expired"
    assert report["should_alert"] is True
    assert "cookie" not in str(report["reason"]).lower()


def test_stale_session_file_over_48h(tmp_path, monkeypatch):
    import os
    import time

    settings = _iso(tmp_path, monkeypatch)
    path = settings.session_path
    path.write_text("{}", encoding="utf-8")
    old = time.time() - 49 * 3600
    os.utime(path, (old, old))
    now = datetime.now(tz=ZoneInfo(settings.timezone))
    report = inspect_session(settings, now=now)
    assert report["status"] == "stale"
    assert report["has_session_file"] is True


def test_stale_when_live_ingest_older_than_48h(tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    record_ingest_success(settings, live=True)
    now = datetime.now(tz=ZoneInfo(settings.timezone))
    report = inspect_session(settings, now=now + timedelta(hours=49))
    assert report["status"] == "stale"
    assert "48" in str(report["reason"])


def test_session_warning_has_no_cookies_and_asks_for_live_ingest(tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    record_ingest_failure(settings, "Set-Cookie: session=secret-value HTTP 403")
    warning = format_session_warning(str(inspect_session(settings)["reason"]))
    blob = warning.lower()
    assert "secret-value" not in blob
    assert "set-cookie" not in blob
    assert "ingest --live" in warning
    sent = maybe_alert_session(settings)
    assert sent is not None
    assert "secret-value" not in sent.lower()
    assert "set-cookie" not in sent.lower()
    assert maybe_alert_session(settings) is None


def test_google_client_loaded_from_env(tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    assert google_client_loaded(settings) is False
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-client-secret")
    from school_secretary.config import reset_settings

    reset_settings()
    from school_secretary.config import get_settings

    loaded = get_settings()
    assert google_client_loaded(loaded) is True
    assert google_configured(loaded) is False


def test_google_push_failure_falls_back_to_ics_without_leaking_secret(tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    init_db(settings)
    tz = ZoneInfo("America/Toronto")
    with session_scope(settings) as session:
        course = Course(org_unit_id="9", code="CISC 235", name="DS", term="F26")
        session.add(course)
        session.flush()
        session.add(
            Assignment(
                course_id=course.id,
                d2l_id="lab",
                title="Lab 2",
                due_at=datetime(2026, 10, 3, 23, 59, tzinfo=tz),
                assignment_type="coding_lab",
            )
        )
        session.add(
            Assignment(
                course_id=course.id,
                d2l_id="mid",
                title="Midterm",
                due_at=datetime(2026, 10, 16, 18, 0, tzinfo=tz),
                assignment_type="quiz",
            )
        )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "refresh-test")
    from school_secretary.config import reset_settings

    reset_settings()
    from school_secretary.config import get_settings

    settings = get_settings()
    assert google_configured(settings)

    def boom(_settings):
        raise RuntimeError("token endpoint rejected super-secret-value")

    monkeypatch.setattr("school_secretary.calendar_sync._access_token", boom)
    result = sync_calendar(settings)
    assert result["transport"] == "ics"
    assert "super-secret-value" not in str(result["error"])
    assert "refresh-test" not in str(result["error"])
    text = Path(result["ics_path"]).read_text(encoding="utf-8")
    assert "Lab 2" in text
    assert "Lab ·" in text
    assert "Test ·" in text or "Midterm" in text


def test_telegram_done_snooze_streak(tmp_path, monkeypatch):
    from school_secretary.agents.orchestrator import format_deadline_alert, run_agents
    from school_secretary.agents.tasks import task_keyboard_rows
    from school_secretary.ingest.fixtures import ingest_fixtures
    from school_secretary.telegram_app.handlers import route_locally

    settings = _iso(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    with session_scope(settings) as session:
        task = session.query(SubTask).filter(SubTask.status != "done").first()
        assert task is not None
        task_id = task.id
        assignment_id = task.assignment_id
    listed = route_locally("/tasks", settings)
    assert str(task_id) in listed
    done = route_locally(f"/done {task_id}", settings)
    assert "Done" in done or "done" in done.lower()
    with session_scope(settings) as session:
        assert session.get(SubTask, task_id).status == "done"
    streak = route_locally("/streak", settings)
    assert "Streak:" in streak
    rows = task_keyboard_rows([task_id])
    flat = {data for row in rows for _, data in row}
    assert f"done:{task_id}" in flat
    assert f"snooze:{task_id}" in flat
    assert "streak" in flat

    tz = ZoneInfo("America/Toronto")
    now = datetime(2026, 9, 14, 12, 0, tzinfo=tz)
    with session_scope(settings) as session:
        course = session.query(Course).filter_by(code="CISC 235").one()
        soon = Assignment(
            course_id=course.id,
            d2l_id="soon-alert",
            title="Quiz tonight",
            due_at=now + timedelta(hours=8),
            assignment_type="quiz",
        )
        session.add(soon)
        session.flush()
        soon_id = soon.id
    snooze = route_locally(f"/snooze a{soon_id}", settings)
    assert "Snoozed" in snooze
    assert format_deadline_alert(settings, now=now) is None


def test_docker_compose_is_headless_telegram_and_raw_ingest():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "telegram" in dockerfile.lower() or "school-secretary" in dockerfile
    assert "playwright install" not in dockerfile.lower()
    assert "telegram:" in compose
    assert "ingest --raw" in compose
    assert "ingest --live" not in compose
    assert "ANTHROPIC_API_KEY" in compose
    assert "TELEGRAM_BOT_TOKEN" in compose
    assert "TELEGRAM_CHAT_ID" in compose
    assert "GOOGLE_OAUTH_CLIENT_ID" in env
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in env
    assert "GOOGLE_OAUTH_REFRESH_TOKEN" in env
