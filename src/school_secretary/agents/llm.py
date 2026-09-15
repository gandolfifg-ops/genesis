from __future__ import annotations

from school_secretary.config import Settings, get_settings

INTEGRITY_RULE = (
    "Academic integrity: outlines, TODO comments, unit-test shells, and repo setup only. "
    "Never write finished essay prose, never implement algorithms, never complete assignments."
)

EXECUTIVE_SYSTEM = (
    "You are My School Secretary, Francesco's personal executive assistant for Queen's onQ. "
    "Clean Markdown with headers and bullets. Actionable, concise, executive-level. "
    "Never mention filenames, local file paths, API keys, or '(Source: …)' labels. "
    f"{INTEGRITY_RULE}"
)


def active_llm_provider(settings: Settings | None = None) -> str:
    """anthropic (preferred), openai, or fallback — never returns secret values."""
    settings = settings or get_settings()
    if (settings.anthropic_api_key or "").strip():
        return "anthropic"
    if (settings.openai_api_key or "").strip():
        return "openai"
    return "fallback"


def complete(system: str, user: str, settings: Settings | None = None) -> str | None:
    """Claude if ANTHROPIC_API_KEY is set, else OpenAI, else None (extractive/templates)."""
    settings = settings or get_settings()
    if (settings.anthropic_api_key or "").strip():
        text = _complete_anthropic(system, user, settings)
        if text:
            return text
    if (settings.openai_api_key or "").strip():
        return _complete_openai(system, user, settings)
    return None


def _complete_anthropic(system: str, user: str, settings: Settings) -> str | None:
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1600,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        blocks = getattr(response, "content", None) or []
        parts = []
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip() or None
    except Exception:
        return None


def _complete_openai(system: str, user: str, settings: Settings) -> str | None:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception:
        return None
