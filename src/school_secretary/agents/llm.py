from __future__ import annotations

from school_secretary.config import get_settings


def complete(system: str, user: str) -> str | None:
    """OpenAI chat if OPENAI_API_KEY is set; otherwise None so callers use local templates."""
    settings = get_settings()
    if not settings.openai_api_key:
        return None
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
        return (response.choices[0].message.content or "").strip()
    except Exception:
        return None


INTEGRITY_RULE = (
    "Academic integrity: outlines, TODO comments, unit-test shells, and repo setup only. "
    "Never write finished essay prose, never implement algorithms, never complete assignments."
)
