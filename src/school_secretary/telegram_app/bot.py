from __future__ import annotations

import sys
import time
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from school_secretary.agents.orchestrator import format_deadline_alert, render_briefing
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


async def _reply(update, text: str, reply_markup=None) -> None:
    body = (text or "")[:4000]
    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    message = update.effective_message or getattr(update, "message", None)
    if message is None:
        return
    try:
        await message.reply_text(body, parse_mode="Markdown", **kwargs)
    except Exception:
        try:
            await message.reply_text(body, **kwargs)
        except Exception:
            return


def _task_markup(settings: Settings):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from school_secretary.agents.tasks import list_open_tasks, task_keyboard_rows
    from school_secretary.db.session import session_scope

    with session_scope(settings) as session:
        ids = [task.id for task in list_open_tasks(session)]
    rows = task_keyboard_rows(ids)
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


async def _cmd_start(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    if update.effective_chat:
        remember_chat_id(settings.telegram_chat_id_path, update.effective_chat.id)
    await _reply(
        update,
        "**My School Secretary** is on.\n\n"
        "Commands: `/ask` `/briefing` `/evening` `/plan` `/scaffold` "
        "`/email` `/study` `/habits` `/calendar` `/done` `/snooze` `/streak` `/tasks`\n\n"
        "Free-text questions go through RAG. I will never complete assignments — outlines and TODOs only.",
    )


async def _cmd_ask(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    question = " ".join(context.args) if context.args else ""
    if not question:
        await _reply(update, "Usage: `/ask` What is the late penalty for CISC 235?")
        return
    await _reply(update, handle_user_text(question, settings))


async def _cmd_briefing(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, render_briefing("morning", settings=settings))


async def _cmd_evening(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, render_briefing("evening", settings=settings))


async def _cmd_plan(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args) or "Lab 2"
    await _reply(update, handle_user_text(f"/plan {target}", settings))


async def _cmd_scaffold(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args) or "Lab 2"
    await _reply(update, handle_user_text(f"/scaffold {target}", settings))


async def _cmd_email(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    topic = " ".join(context.args) or "office hours"
    await _reply(update, handle_user_text(f"/email {topic}", settings))


async def _cmd_study(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, handle_user_text("/study " + " ".join(context.args), settings))


async def _cmd_calendar(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, handle_user_text("/calendar", settings))


async def _cmd_habits(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, handle_user_text("/habits", settings), reply_markup=_task_markup(settings))


async def _cmd_done(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args)
    await _reply(update, handle_user_text(f"/done {target}", settings), reply_markup=_task_markup(settings))


async def _cmd_snooze(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    target = " ".join(context.args)
    await _reply(update, handle_user_text(f"/snooze {target}", settings), reply_markup=_task_markup(settings))


async def _cmd_streak(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, handle_user_text("/streak", settings), reply_markup=_task_markup(settings))


async def _cmd_tasks(update, context) -> None:
    settings: Settings = context.bot_data["settings"]
    await _reply(update, handle_user_text("/tasks", settings), reply_markup=_task_markup(settings))


async def _on_callback(update, context) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    settings: Settings = context.bot_data["settings"]
    data = query.data
    try:
        await query.answer()
    except Exception:
        pass
    if data.startswith("done:"):
        text = handle_user_text(f"/done {data.split(':', 1)[1]}", settings)
    elif data.startswith("snooze:"):
        text = handle_user_text(f"/snooze {data.split(':', 1)[1]}", settings)
    elif data == "streak":
        text = handle_user_text("/streak", settings)
    elif data.startswith("study:"):
        minutes = data.split(":", 1)[1]
        text = handle_user_text(f"/study {minutes}", settings)
    else:
        text = handle_user_text("/tasks", settings)
    markup = _task_markup(settings)
    body = (text or "")[:4000]
    try:
        await query.edit_message_text(body, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        try:
            await query.edit_message_text(body, reply_markup=markup)
        except Exception:
            await _reply(update, body, reply_markup=markup)


async def _on_text(update, context) -> None:
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return
    settings: Settings = context.bot_data["settings"]
    if update.effective_chat:
        remember_chat_id(settings.telegram_chat_id_path, update.effective_chat.id)
    await _reply(update, handle_user_text(update.message.text, settings))


async def _on_error(update, context) -> None:
    print("Telegram handler error; continuing to poll.", file=sys.stderr)
    if update and getattr(update, "message", None):
        await _reply(update, "Something went wrong on my side. Try that again in a moment.")


async def _send_chat(context, text: str, reply_markup=None) -> None:
    settings: Settings = context.bot_data["settings"]
    chat_id = _target_chat_id(settings)
    if not chat_id or not text:
        return
    body = text[:4000]
    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await context.bot.send_message(chat_id=int(chat_id), text=body, parse_mode="Markdown", **kwargs)
    except Exception:
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=body, **kwargs)
        except Exception:
            return


async def _job_briefing(context, kind: str) -> None:
    settings: Settings = context.bot_data["settings"]
    await _send_chat(context, render_briefing(kind, settings=settings))


async def _job_deadline_alerts(context) -> None:
    settings: Settings = context.bot_data["settings"]
    text = format_deadline_alert(settings)
    if text:
        await _send_chat(context, text, reply_markup=_task_markup(settings))


async def _job_session_guard(context) -> None:
    settings: Settings = context.bot_data["settings"]
    from school_secretary.ingest.session_guard import maybe_alert_session

    maybe_alert_session(settings)


def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        print(MISSING_TOKEN, file=sys.stderr)
        raise SystemExit(1)

    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    tz = ZoneInfo(settings.timezone)
    application = Application.builder().token(token).build()
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", _cmd_start))
    application.add_handler(CommandHandler("help", _cmd_start))
    application.add_handler(CommandHandler("ask", _cmd_ask))
    application.add_handler(CommandHandler("briefing", _cmd_briefing))
    application.add_handler(CommandHandler("evening", _cmd_evening))
    application.add_handler(CommandHandler("plan", _cmd_plan))
    application.add_handler(CommandHandler("scaffold", _cmd_scaffold))
    application.add_handler(CommandHandler("email", _cmd_email))
    application.add_handler(CommandHandler("study", _cmd_study))
    application.add_handler(CommandHandler("calendar", _cmd_calendar))
    application.add_handler(CommandHandler("habits", _cmd_habits))
    application.add_handler(CommandHandler("done", _cmd_done))
    application.add_handler(CommandHandler("snooze", _cmd_snooze))
    application.add_handler(CommandHandler("streak", _cmd_streak))
    application.add_handler(CommandHandler("tasks", _cmd_tasks))
    application.add_handler(CallbackQueryHandler(_on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    application.add_error_handler(_on_error)

    jobs = application.job_queue
    if jobs is None:
        print("JobQueue is unavailable; briefings will not be scheduled.", file=sys.stderr)
    else:

        async def morning_job(ctx) -> None:
            await _job_briefing(ctx, "morning")

        async def evening_job(ctx) -> None:
            await _job_briefing(ctx, "evening")

        async def deadline_job(ctx) -> None:
            await _job_deadline_alerts(ctx)

        async def session_job(ctx) -> None:
            await _job_session_guard(ctx)

        jobs.run_daily(
            morning_job,
            time=dt_time(8, 0, tzinfo=tz),
            name="morning-briefing",
        )
        jobs.run_daily(
            evening_job,
            time=dt_time(20, 0, tzinfo=tz),
            name="evening-wrapup",
        )
        jobs.run_repeating(
            deadline_job,
            interval=3600,
            first=30,
            name="deadline-24h",
        )
        jobs.run_repeating(
            session_job,
            interval=6 * 3600,
            first=90,
            name="session-guard",
        )
        print("Scheduled 08:00 briefing, 20:00 check-in, hourly 24h alerts, and session-guard (America/Toronto).")

    print("Polling Telegram (auto-reconnect). Ctrl+C to stop.")
    backoff = 4
    while True:
        try:
            application.run_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=False,
                bootstrap_retries=5,
            )
            break
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception:
            print(f"Telegram disconnected; retrying in {backoff}s.", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
