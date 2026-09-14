from school_secretary.db.live import FIXTURE_ORG_UNIT_IDS, live_course_ids
from school_secretary.db.models import Course
from school_secretary.db.session import init_db, session_scope
from school_secretary.ingest.brightspace import (
    SESSION_EXPIRED,
    _session_expired_error,
    attachment_api_path,
    parse_onq_api_response,
)
from school_secretary.ingest.fixtures import ingest_fixtures


def test_attachment_api_paths_have_no_query_string():
    news = attachment_api_path("news", "100", "9", "3")
    drop = attachment_api_path("dropbox", "100", "8", "2")
    assert news == "/d2l/api/le/1.47/100/news/9/attachments/3"
    assert drop == "/d2l/api/le/1.47/100/dropbox/folders/8/attachments/2"
    assert "?" not in news and "?" not in drop


def test_session_expired_error_has_no_body():
    err = _session_expired_error(403, "text/html; charset=utf-8")
    text = str(err)
    assert "HTTP 403" in text
    assert "text/html" in text
    assert SESSION_EXPIRED.split(".")[0] in text
    assert "cookie" not in text.lower()
    assert "token" not in text.lower()


def test_live_course_ids_ignore_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    settings = get_settings()
    init_db(settings)
    with session_scope(settings) as session:
        session.add(Course(org_unit_id="66123", code="CISC 235", name="fixture", term="F26"))
        session.add(Course(org_unit_id="999001", code="CISC 999", name="live", term="F26"))
        session.flush()
        ids = live_course_ids(session)
        assert ids is not None
        live = session.get(Course, ids[0])
        assert live is not None
        assert live.org_unit_id not in FIXTURE_ORG_UNIT_IDS
        assert live.code == "CISC 999"


def test_parse_onq_json_ok_and_403_omits_body():
    payload = parse_onq_api_response(200, "application/json", '{"Items":[]}')
    assert payload == {"Items": []}
    try:
        parse_onq_api_response(403, "application/json", '{"secret":"do-not-leak"}')
        raise AssertionError("expected IngestError")
    except Exception as exc:
        text = str(exc)
        assert "403" in text
        assert "do-not-leak" not in text
        assert "cookie" not in text.lower()


def test_live_ingest_source_does_not_use_httpx():
    from pathlib import Path

    text = Path("src/school_secretary/ingest/brightspace.py").read_text(encoding="utf-8")
    assert "httpx" not in text
    assert "PlaywrightLEClient" in text
    assert "context.request.get" in text


def test_live_ingest_opens_onq_home_networkidle_before_apis():
    from pathlib import Path

    text = Path("src/school_secretary/ingest/brightspace.py").read_text(encoding="utf-8")
    home_idx = text.find('wait_until="networkidle"')
    api_idx = text.find("await client.my_enrollments()")
    assert "/d2l/home" in text
    assert home_idx != -1
    assert api_idx != -1
    assert home_idx < api_idx


def test_ingest_live_requires_storage_state(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCHOOL_SECRETARY_SESSION_PATH", str(tmp_path / "storage_state.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine
    from school_secretary.ingest.brightspace import IngestError, ingest_live

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    try:
        ingest_live(get_settings())
        raise AssertionError("expected IngestError")
    except IngestError as exc:
        assert "login" in str(exc).lower()
        assert "cookie" not in str(exc).lower()


def test_fixture_ingest_still_skips_playwright(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    counts = ingest_fixtures(get_settings())
    assert counts["courses"] == 3
    assert counts["assignments"] == 3
