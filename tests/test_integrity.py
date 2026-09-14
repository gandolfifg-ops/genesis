from school_secretary.agents.llm import INTEGRITY_RULE
from school_secretary.telegram_app.handlers import route_locally


def test_integrity_rule_present():
    assert "Never write finished" in INTEGRITY_RULE or "Never" in INTEGRITY_RULE


def test_help_route_states_scaffolding_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOOL_SECRETARY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from school_secretary.config import reset_settings
    from school_secretary.db.session import reset_engine

    reset_settings()
    reset_engine()
    from school_secretary.config import get_settings

    text = route_locally("/help", get_settings())
    assert "outlines" in text.lower() or "todo" in text.lower()
    assert "never complete" in text.lower()
