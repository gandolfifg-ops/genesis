import pytest


@pytest.fixture(autouse=True)
def _no_live_llm_keys(monkeypatch):
    """Tests stay extractive unless they set a fake key themselves."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
