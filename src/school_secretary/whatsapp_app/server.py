from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from school_secretary.config import Settings, get_settings
from school_secretary.telegram_app.handlers import handle_user_text

GRAPH = "https://graph.facebook.com/v21.0"


def cloud_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.whatsapp_token and settings.whatsapp_phone_number_id)


def _append_outbox(settings: Settings, direction: str, text: str, peer: str = "") -> None:
    settings.ensure_dirs()
    row = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "direction": direction,
        "peer": peer,
        "text": text[:4000],
        "transport": "cloud" if cloud_configured(settings) else "mock",
    }
    with settings.whatsapp_outbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def send_cloud_text(settings: Settings, to: str, body: str) -> None:
    url = f"{GRAPH}/{settings.whatsapp_phone_number_id}/messages"
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {settings.whatsapp_token}",
            "Content-Type": "application/json",
        },
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body[:4000]},
        },
        timeout=20.0,
    )
    response.raise_for_status()


def reply_to_text(text: str, settings: Settings, peer: str = "") -> str:
    reply = handle_user_text(text, settings)
    _append_outbox(settings, "in", text, peer=peer)
    _append_outbox(settings, "out", reply, peer=peer)
    if cloud_configured(settings) and peer:
        try:
            send_cloud_text(settings, peer, reply)
        except Exception as exc:
            _append_outbox(settings, "error", str(exc), peer=peer)
    return reply


MISSING_CLOUD = """\
WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID are not set.

Telegram stays primary (@Queens_onQ_assistant_bot). This process runs a local
mock webhook so the same commands work without Meta credentials:

  curl -sS -X POST http://127.0.0.1:43148/mock/message \\
    -H 'content-type: application/json' \\
    -d '{"text":"/briefing"}'

To use WhatsApp Cloud API, set in repo-root .env:
  WHATSAPP_TOKEN=
  WHATSAPP_PHONE_NUMBER_ID=
  WHATSAPP_VERIFY_TOKEN=
  WHATSAPP_WEBHOOK_HOST=127.0.0.1
  WHATSAPP_WEBHOOK_PORT=43148
"""


def build_app(settings: Settings | None = None):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    settings = settings or get_settings()

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "transport": "cloud" if cloud_configured(settings) else "mock",
                "commands": ["/start", "/ask", "/briefing", "/evening", "/plan", "/scaffold", "/email", "/study"],
            }
        )

    async def verify(request: Request) -> PlainTextResponse:
        mode = request.query_params.get("hub.mode", "")
        token = request.query_params.get("hub.verify_token", "")
        challenge = request.query_params.get("hub.challenge", "")
        if mode == "subscribe" and token and token == settings.whatsapp_verify_token:
            return PlainTextResponse(challenge)
        return PlainTextResponse("forbidden", status_code=403)

    async def inbound(request: Request) -> JSONResponse:
        payload = await request.json()
        texts: list[tuple[str, str]] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                for msg in value.get("messages") or []:
                    body = (msg.get("text") or {}).get("body") or ""
                    peer = msg.get("from") or ""
                    if body:
                        texts.append((body, peer))
        replies = [reply_to_text(body, settings, peer=peer) for body, peer in texts]
        return JSONResponse({"ok": True, "replies": len(replies)})

    async def mock_message(request: Request) -> JSONResponse:
        payload = await request.json()
        text = str(payload.get("text") or "")
        peer = str(payload.get("from") or "mock")
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)
        reply = reply_to_text(text, settings, peer=peer)
        return JSONResponse({"reply": reply, "outbox": str(settings.whatsapp_outbox_path)})

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/webhook", verify, methods=["GET"]),
            Route("/webhook", inbound, methods=["POST"]),
            Route("/mock/message", mock_message, methods=["POST"]),
        ]
    )


def run_whatsapp(settings: Settings | None = None) -> None:
    import uvicorn

    settings = settings or get_settings()
    if not cloud_configured(settings):
        print(MISSING_CLOUD)
    else:
        print(
            f"WhatsApp Cloud webhook on http://{settings.whatsapp_webhook_host}:{settings.whatsapp_webhook_port}/webhook"
        )
    app = build_app(settings)
    uvicorn.run(
        app,
        host=settings.whatsapp_webhook_host,
        port=settings.whatsapp_webhook_port,
        log_level="info",
    )
