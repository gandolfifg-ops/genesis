from school_secretary.agents.llm import active_llm_provider, complete
from school_secretary.config import reset_settings


def test_complete_is_none_without_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reset_settings()
    from school_secretary.config import get_settings

    settings = get_settings()
    assert active_llm_provider(settings) == "fallback"
    assert complete("sys", "user", settings=settings) is None


def test_provider_prefers_anthropic_over_openai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    reset_settings()
    from school_secretary.config import get_settings

    assert active_llm_provider(get_settings()) == "anthropic"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    reset_settings()
    assert active_llm_provider(get_settings()) == "openai"
