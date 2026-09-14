from pathlib import Path

from school_secretary.agents.drafting import scaffold_assignment
from school_secretary.agents.orchestrator import ask, render_briefing, run_agents
from school_secretary.config import reset_settings
from school_secretary.db.session import init_db, reset_engine
from school_secretary.ingest.fixtures import DEMO_NOW, ingest_fixtures
from school_secretary.telegram_app.bot import MISSING_TOKEN, run_bot


def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    return get_settings()


def test_offline_demo_rag_plan_briefing(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path, monkeypatch)
    counts = ingest_fixtures(settings)
    assert counts["courses"] == 3
    assert counts["assignments"] == 3
    run_agents(settings)
    answer = ask("What is the late penalty for CISC 235?", settings=settings)
    lowered = answer.lower()
    assert "10%" in answer or "10 %" in answer or "10 percent" in lowered
    assert "3" in answer
    from school_secretary.agents.orchestrator import plan_and_format, scaffold_and_describe

    plan = plan_and_format("Binary Search", settings=settings)
    assert "micro" in plan.lower() or "implement" in plan.lower()
    assert "Lab 2" in plan or "Binary" in plan
    described = scaffold_and_describe("Binary Search", settings=settings)
    assert "TODO" in described or "scaffold" in described.lower()
    briefing = render_briefing("morning", settings=settings, now=DEMO_NOW)
    assert "CISC 235" in briefing
    assert "extended" in briefing.lower() or "Lab 1" in briefing


def test_coding_scaffold_is_not_a_solution(tmp_path, monkeypatch):
    settings = _isolated_settings(tmp_path, monkeypatch)
    ingest_fixtures(settings)
    run_agents(settings)
    from school_secretary.agents.orchestrator import find_assignment
    from school_secretary.db.session import session_scope

    with session_scope(settings) as session:
        assignment = find_assignment(session, "Binary Search")
        assert assignment is not None
        dest = scaffold_assignment(assignment, settings)
    bst = (Path(dest) / "bst.py").read_text(encoding="utf-8")
    assert "NotImplementedError" in bst
    assert "# TODO" in bst
    assert "ACADEMIC INTEGRITY" in bst
    # Must not ship a working insert loop / recursive insert body.
    assert "while current" not in bst
    tests = (Path(dest) / "test_bst.py").read_text(encoding="utf-8")
    assert "pytest.mark.skip" in tests


def test_telegram_without_token_exits_clearly(tmp_path, monkeypatch, capsys):
    settings = _isolated_settings(tmp_path, monkeypatch)
    try:
        run_bot(settings)
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert exc.code == 1
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN is not set" in err
    assert "BotFather" in MISSING_TOKEN
