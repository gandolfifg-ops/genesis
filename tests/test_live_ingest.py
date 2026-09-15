from school_secretary.db.live import FIXTURE_ORG_UNIT_IDS, live_course_ids
from school_secretary.db.models import Announcement, Assignment, Course
from school_secretary.db.session import init_db, session_scope
from school_secretary.ingest.brightspace import (
    SESSION_EXPIRED,
    _session_expired_error,
    attachment_api_path,
    classify_onq_json_payload,
    classify_onq_json_url,
    dropbox_from_dom,
    enrollment_from_tile,
    ingest_payloads,
    news_from_dom,
    org_id_from_onq_url,
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


def test_live_ingest_source_does_not_use_httpx_or_scripted_api():
    from pathlib import Path

    text = Path("src/school_secretary/ingest/brightspace.py").read_text(encoding="utf-8")
    assert "httpx" not in text
    assert "PlaywrightLEClient" not in text
    assert "context.request.get" not in text
    assert "request.get" not in text
    assert "evaluate(fetch" not in text
    assert 'page.on("response"' in text
    assert "page.locator" in text
    assert "scrape_course_tiles" in text
    assert "/d2l/home" in text


def test_live_ingest_opens_onq_home_networkidle_before_scrape():
    from pathlib import Path

    text = Path("src/school_secretary/ingest/brightspace.py").read_text(encoding="utf-8")
    home_idx = text.find('wait_until="networkidle"')
    scrape_idx = text.find("await scrape_course_tiles(page)")
    assert "/d2l/home" in text
    assert home_idx != -1
    assert scrape_idx != -1
    assert home_idx < scrape_idx


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


def test_ingest_live_defaults_headed_and_reuses_persistent_context():
    import inspect
    from pathlib import Path

    from school_secretary.ingest.brightspace import ingest_live, ingest_live_from_context

    params = inspect.signature(ingest_live).parameters
    assert params["headed"].default is True
    assert inspect.iscoroutinefunction(ingest_live_from_context)

    brightspace = Path("src/school_secretary/ingest/brightspace.py").read_text(encoding="utf-8")
    assert "ingest_live_from_context" in brightspace
    assert "_open_persistent_context" in brightspace
    assert "headless=False" in brightspace
    assert "httpx" not in brightspace

    browser = Path("src/school_secretary/ingest/browser.py").read_text(encoding="utf-8")
    assert "ingest_live_from_context" in browser
    assert "user_data_dir" in browser
    assert "headless=False" in browser
    assert "data/browser" in browser or "browser_profile_dir" in browser

    cli = Path("src/school_secretary/cli.py").read_text(encoding="utf-8")
    assert "--headed/--headless" in cli
    assert "headed=headed" in cli


def test_classify_natural_onq_xhr_and_dom_payloads(tmp_path, monkeypatch):
    assert classify_onq_json_url("https://onq.queensu.ca/d2l/api/lp/1.47/enrollments/myenrollments/") == "enrollments"
    assert classify_onq_json_url("https://onq.queensu.ca/d2l/api/le/1.47/999001/news/") == "news"
    assert classify_onq_json_url("https://onq.queensu.ca/d2l/api/le/1.47/999001/dropbox/folders/") == "dropbox"
    assert org_id_from_onq_url("https://onq.queensu.ca/d2l/home/999001") == "999001"
    assert org_id_from_onq_url("https://onq.queensu.ca/d2l/lms/dropbox/user/folders_list.d2l?ou=999001") == "999001"

    enrollments = {"Items": [enrollment_from_tile("999001", "CISC 999 Algorithms")]}
    assert classify_onq_json_payload(enrollments) == "enrollments"
    assert classify_onq_json_payload({"Items": [news_from_dom("Hello", "Body", "n1")]}) == "news"
    assert classify_onq_json_payload({"Items": [dropbox_from_dom("Lab 1", "2026-09-20T23:59:00Z", "Do the lab", "d1")]}) == "dropbox"

    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings
    from school_secretary.db.models import Assignment, Announcement, Course
    from school_secretary.db.session import session_scope
    from school_secretary.ingest.parser import parse_enrollment_item

    settings = get_settings()
    init_db(settings)
    tile = enrollment_from_tile("999001", "CISC 999 Algorithms")
    parsed = parse_enrollment_item(tile)
    assert parsed is not None
    assert parsed["code"] == "CISC 999"
    counts = ingest_payloads(
        settings,
        [tile],
        {"999001": [news_from_dom("Office hours", "Moved to Goodwin", "n1")]},
        {"999001": [dropbox_from_dom("Lab 1", "2026-09-20T23:59:00Z", "Implement the lab", "d1")]},
    )
    assert counts["courses"] == 1
    assert counts["announcements"] == 1
    assert counts["assignments"] == 1
    with session_scope(settings) as session:
        course = session.query(Course).one()
        assert course.code == "CISC 999"
        assert session.query(Announcement).one().title == "Office hours"
        assert session.query(Assignment).one().title == "Lab 1"


def test_duplicate_course_codes_do_not_crash_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings
    from school_secretary.agents.orchestrator import find_course
    from school_secretary.db.live import course_by_code
    from school_secretary.rag.index import index_documents
    from school_secretary.rag.query import answer_question, detect_course

    settings = get_settings()
    init_db(settings)
    with session_scope(settings) as session:
        session.add(Course(org_unit_id="111", code="APSC 141", name="Programming 1", term="F26"))
        session.add(Course(org_unit_id="222", code="APSC 141", name="Programming 1 lab", term="F26"))
        session.flush()
        session.add(
            Announcement(
                course_id=session.query(Course).filter_by(org_unit_id="222").one().id,
                d2l_id="n1",
                title="Lab tools",
                body="Bring a laptop to the APSC 141 lab. No late labs without consideration.",
            )
        )
        assert course_by_code(session, "APSC 141") is not None
        assert detect_course(session, "late labs in APSC 141") is not None
        assert find_course(session, "APSC 141") is not None
    index_documents(settings, force=True)
    answer = answer_question("What should I bring to the APSC 141 lab?", settings=settings)
    assert "laptop" in answer.lower()


def test_xhr_capture_keeps_natural_json_and_ignores_403():
    import asyncio

    from school_secretary.ingest.brightspace import OnqXhrCapture
    from school_secretary.ingest.parser import parse_enrollment_item

    class Ok:
        status = 200
        url = "https://onq.queensu.ca/d2l/api/lp/1.47/enrollments/myenrollments/"
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"Items": [enrollment_from_tile("888001", "CISC 365 Algorithms")]}

    class Forbidden:
        status = 403
        url = "https://onq.queensu.ca/d2l/api/le/1.47/888001/news/"
        headers = {"content-type": "application/json"}

        async def json(self):
            raise AssertionError("403 bodies must not be read")

    capture = OnqXhrCapture()
    asyncio.run(capture.ingest_response(Ok()))
    asyncio.run(capture.ingest_response(Forbidden()))
    assert len(capture.enrollments) == 1
    parsed = parse_enrollment_item(capture.enrollments[0])
    assert parsed is not None
    assert parsed["code"] == "CISC 365"
    assert capture.news_by_org == {}
