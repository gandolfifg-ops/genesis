from __future__ import annotations

import sys
from datetime import time
from zoneinfo import ZoneInfo

from school_secretary.agents.orchestrator import render_briefing
from school_secretary.config import Settings, get_settings
from school_secretary.telegram_app.handlers import handle_user_text, load_chat_id, remember_chat_id

MISSING_TOKEN = """\
TELEGRAM_BOT_TOKEN is not set.

How to get one:
  1. Open Telegram and chat with @BotFather (https://t.me/BotFather)
  2. Send /newbot and follow the prompts
  3. Copy the token BotFather returns

Then, in this repo root:
  cp .env.example .env
  # edit .env and set:
  TELEGRAM_BOT_TOKEN=123456:ABC...

Start the bot:
  uv run school-secretary telegram

The offline demo does not need Telegram:
  uv run school-secretary demo
"""


def _target_chat_id(settings: Settings) -> str:
    return load_chat_id(settings.telegram_chat_id_path, settings.telegram_chat_id)


async def _cmd_start(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    if update.effective_chat:
        remember_chat_id(settings.telegram_chat_id_path, update.effective_chat.id)
    await update.message.reply_text(
        "My School Secretary is on. Commands: /ask /briefing /evening /plan /scaffold /email /study\n"
        "Free-text questions go through RAG (OpenAI if keyed, otherwise local extractive answers).\n"
        "I will never complete assignments — outlines and TODOs only."
    )


async def _cmd_ask(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Usage: /ask What is the late penalty for CISC 235?")
        return
    await update.message.reply_text(handle_user_text(question, settings)[:4000])


async def _cmd_briefing(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await update.message.reply_text(render_briefing("morning", settings=settings)[:4000])


async def _cmd_evening(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await update.message.reply_text(render_briefing("evening", settings=settings)[:4000])


async def _cmd_plan(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args) or "Lab 2"
    await update.message.reply_text(handle_user_text(f"/plan {target}", settings)[:4000])


async def _cmd_scaffold(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args) or "Lab 2"
    await update.message.reply_text(handle_user_text(f"/scaffold {target}", settings)[:4000])


async def _cmd_email(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    topic = " ".join(context.args) or "office hours"
    await update.message.reply_text(handle_user_text(f"/email {topic}", settings)[:4000])


async def _cmd_study(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await update.message.reply_text(handle_user_text("/study " + " ".join(context.args), settings)[:4000])


async def _on_text(update, context) -> None:
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return
    settings: Settings = context.bot_data["settings"]
    if update.effective_chat:
        remember_chat_id(settings.telegram_chat_id_path, update.effective_chat.id)
    await update.message.reply_text(handle_user_text(update.message.text, settings)[:4000])


async def _job_briefing(context, kind: str) -> None:
    settings: Settings = context.bot_data["settings"]
    chat_id = _target_chat_id(settings)
    if not chat_id:
        return
    text = render_briefing(kind, settings=settings)
    await context.bot.send_message(chat_id=int(chat_id), text=text[:4000])


def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        print(MISSING_TOKEN, file=sys.stderr)
        raise SystemExit(1)

    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    tz = ZoneInfo(settings.timezone)
    application = Application.builder().token(token).build()
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", _cmd_start))
    application.add_handler(CommandHandler("ask", _cmd_ask))
    application.add_handler(CommandHandler("briefing", _cmd_briefing))
    application.add_handler(CommandHandler("evening", _cmd_evening))
    application.add_handler(CommandHandler("plan", _cmd_plan))
    application.add_handler(CommandHandler("scaffold", _cmd_scaffold))
    application.add_handler(CommandHandler("email", _cmd_email))
    application.add_handler(CommandHandler("study", _cmd_study))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))

    jobs = application.job_queue
    if jobs is None:
        print("JobQueue is unavailable; briefings will not be scheduled.", file=sys.stderr)
    else:

        async def morning_job(ctx) -> None:
            await _job_briefing(ctx, "morning")

        async def evening_job(ctx) -> None:
            await _job_briefing(ctx, "evening")

        jobs.run_daily(
            morning_job,
            time=time(8, 0, tzinfo=tz),
            name="morning-briefing",
        )
        jobs.run_daily(
            evening_job,
            time=time(20, 0, tzinfo=tz),
            name="evening-wrapup",
        )
        print("Scheduled morning briefing 08:00 and evening wrap-up 20:00 America/Toronto.")

    print("Polling Telegram. Ctrl+C to stop.")
    application.run_polling(allowed_updates=["message"])
