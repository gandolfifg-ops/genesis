from __future__ import annotations

import httpx

from school_secretary.agents.persona import polish_outgoing
from school_secretary.config import Settings
from school_secretary.telegram_app.handlers import load_chat_id


def send_telegram_text(settings: Settings, text: str, reply_markup: dict | None = None) -> bool:
    """Push Markdown to TELEGRAM_CHAT_ID / remembered chat. Never logs the token."""
    token = (settings.telegram_bot_token or "").strip()
    chat_id = load_chat_id(settings.telegram_chat_id_path, settings.telegram_chat_id)
    body = polish_outgoing(text or "")[:4000]
    if not token or not chat_id or not body:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {"chat_id": int(chat_id), "text": body, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = httpx.post(url, json=payload, timeout=20.0)
        if response.status_code >= 400:
            payload.pop("parse_mode", None)
            response = httpx.post(url, json=payload, timeout=20.0)
        return response.status_code < 400
    except Exception:
        return False


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }
