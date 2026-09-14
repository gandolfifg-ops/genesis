# My School Secretary

Local multi-agent RAG assistant for Queen’s onQ (Brightspace). It ingests courses, indexes PDFs, triages announcements, plans assignments into micro-deadlines, writes **scaffolding only** (outlines, `# TODO` stubs, test shells), and talks over Telegram plus a Cursor MCP server.

Nothing in this slice completes student work. Coding labs get `NotImplementedError` stubs; essays get section outlines with word counts from the rubric.

**You do not need Queen’s login, Telegram, or OpenAI to try it.** Fixtures cover three Fall 2026 courses (CISC 235, CISC 365, PHIL 111).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Offline demo (no credentials)

From the repository root:

```bash
uv sync --group dev
cp .env.example .env
uv run school-secretary demo
```

That command:

1. Writes Brightspace-shaped JSON + PDFs under `data/raw/` and SQLite at `data/secretary.db`
2. Embeds syllabi/labs/rubrics into ChromaDB at `data/chroma/`
3. Answers course-scoped late-penalty questions (CISC 235: 10%/day, 3-day cap; CISC 365: 5%/day, 2 days)
4. Prints a Lab 2 plan with micro-deadlines (habit-aware: front-load if you logged 0 min)
5. Writes a BST scaffold under `data/scaffolds/` (stubs only)
6. Prints a morning briefing (announcements, due dates, study-habit summary, next micro-deadlines, ICS path)
7. Writes `data/calendar/school-secretary.ics` (Google OAuth optional)

Useful follow-ups:

```bash
uv run school-secretary query "What is the late penalty for CISC 365?"
uv run school-secretary plan "Lab 2"
uv run school-secretary scaffold "Essay 1"
uv run school-secretary briefing morning
uv run school-secretary briefing evening
uv run school-secretary habits
uv run school-secretary status
uv run school-secretary study --course "CISC 235" --minutes 45 --notes "BST traces"
uv run school-secretary calendar-sync
uv run school-secretary email --topic "clarification on Lab 2 test cases" --course "CISC 235"
uv run pytest
```

Re-running `demo` or `ingest --fixtures` is idempotent (same D2L ids upsert).

## Environment files

| File | Where | Purpose |
| --- | --- | --- |
| `.env.example` | repository root (committed) | Template with every variable this repo reads |
| `.env` | repository root (gitignored) | Your real secrets. Already present with placeholders; or `cp .env.example .env` |

Variable names (exact):

| Name | Required? | Used by |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | only for `school-secretary telegram` | python-telegram-bot |
| `TELEGRAM_CHAT_ID` | optional | proactive 8:00 / 20:00 briefings if you never sent `/start` |
| `OPENAI_API_KEY` | optional | function calling + richer RAG synthesis |
| `ANTHROPIC_API_KEY` | optional | reserved; OpenAI is used if keyed, otherwise local/mock |
| `OPENAI_MODEL` | optional, default `gpt-4o-mini` | OpenAI model id |
| `ONQ_BASE_URL` | optional, default `https://onq.queensu.ca` | live ingest |
| `MCP_HOST` / `MCP_PORT` | optional, default `127.0.0.1` / `43147` | MCP HTTP bind |
| `GOOGLE_CALENDAR_ID` | optional, default `primary` | Calendar API calendar id |
| `GOOGLE_OAUTH_CLIENT_ID` | optional | Calendar OAuth client |
| `GOOGLE_OAUTH_CLIENT_SECRET` | optional | Calendar OAuth secret |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | optional | Calendar refresh token; if empty, sync writes a local ICS |
| `WHATSAPP_TOKEN` | optional | Meta Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | optional | Meta Cloud phone number id |
| `WHATSAPP_VERIFY_TOKEN` | optional | webhook verify token |
| `WHATSAPP_WEBHOOK_HOST` / `WHATSAPP_WEBHOOK_PORT` | optional, default `127.0.0.1` / `43148` | WhatsApp webhook bind |

If OpenAI is unset, agents use extractive RAG + templates. If Telegram is unset, `telegram` exits with the setup text below; everything else still runs.

---

## 1. Telegram Bot Token (BotFather)

The bot **starts without a token only to tell you how to get one** (exit code 1). It does not crash. With a token it polls and schedules briefings.

**Where to get the token**

1. Open Telegram (phone or desktop).
2. Search for **`@BotFather`** or open https://t.me/BotFather
3. Send `/newbot`
4. Pick a display name (e.g. `My School Secretary`) and a username ending in `bot`
5. BotFather replies with a token that looks like `123456789:AAH...`

**Exact `.env` variable this repo uses**

```bash
TELEGRAM_BOT_TOKEN="your_bot_token_here"
```

A gitignored `.env` at the repo root is created with that placeholder (and the other keys from `.env.example`). Replace the placeholder with BotFather’s token.

Optional, for unsolicited morning/evening messages before you have chatted with the bot:

```bash
TELEGRAM_CHAT_ID=111111111
```

If `TELEGRAM_CHAT_ID` is empty, send `/start` to the bot once. The chat id is stored at `data/telegram_chat_id.txt` and later briefings use it.

**Where the files live**

```bash
# repository root
cp .env.example .env
```

Edit `.env` (same folder as `pyproject.toml`). Do not put the token in `.env.example`.

**How to start the bot**

```bash
# seed local data first so /ask and /briefing have courses
uv run school-secretary demo

uv run school-secretary telegram
```

Without a token you should see a stderr message that starts with `TELEGRAM_BOT_TOKEN is not set` and the process exits `1`.

With a token the process polls Telegram and prints that it scheduled:

- morning briefing **08:00 America/Toronto**
- evening wrap-up **20:00 America/Toronto**

Commands: `/start` `/help` `/ask` `/briefing` `/evening` `/plan` `/scaffold` `/email` `/study` `/habits` `/calendar` plus free-text questions.

---

## 2. Playwright Queen’s SSO (Windows WSL)

Live onQ ingest is **optional**. The fixture demo never opens a browser.

Do this in **Ubuntu WSL**, not PowerShell. Clone `francescog11/genesis`, then from the repo root:

```bash
cd ~/genesis   # or wherever you cloned francescog11/genesis
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv sync --group dev
uv run playwright install chromium
echo "$DISPLAY"    # Windows 11 WSLg should print something like :0

# Offline proof (no Queen’s / Telegram / Google / WhatsApp secrets):
uv run school-secretary demo
uv run pytest

# Live onQ — headed Chromium on your Windows desktop:
uv run school-secretary login
# NetID, password, Duo, wait for onQ homepage, press Enter in this WSL terminal
uv run school-secretary ingest --live

# Optional after ingest:
uv run school-secretary calendar-sync
uv run school-secretary habits
uv run school-secretary status
uv run school-secretary whatsapp    # mock on :43148 unless Meta keys are set
uv run school-secretary telegram    # needs TELEGRAM_BOT_TOKEN in .env
```

If `echo $DISPLAY` is empty:

```bash
export DISPLAY=:0
uv run school-secretary login
```

**Exact one-time login command**

```bash
uv run school-secretary login
```

**What you should see**

1. A **real Chromium** window opens on your Windows desktop (Playwright persistent profile, 1280×900).
2. It goes to the Queen’s onQ login at **`https://onq.queensu.ca/d2l/home`**.
3. Type your **NetID** and **password**, then approve **Duo MFA**.
4. When you reach the **onQ homepage** (course tiles / Brightspace navbar), leave the window open, switch back to the **WSL terminal**, and **press Enter**.

**Where cookies are saved**

| Path | What |
| --- | --- |
| `storage_state.json` | Playwright `storage_state` (cookies + origins), **repository root**, gitignored — this is the file ingest reads |
| `session.json` | Copy of the same JSON (also gitignored) so anything still looking for the old name keeps working |
| `data/browser/` | Persistent Chromium user-data dir for the same profile |

The login command prints these paths when it finishes. Duo is only needed during this one visible login.

**Silent / headless refresh after that**

```bash
uv run school-secretary ingest --live
```

This does **not** open a window and should **not** prompt Duo again. It:

1. Starts **headless Chromium** with the same persistent profile and `storage_state.json` (same cookies, user-agent, and CSRF as login)
2. Opens `{ONQ_BASE_URL}/d2l/home` with `wait_until="networkidle"` so `d2lSessionVal` is set, then calls Brightspace LP/LE APIs **through that browser context** (not a standalone HTTP client):
   - `/d2l/api/lp/1.47/enrollments/myenrollments/`
   - `/d2l/api/le/1.47/{orgUnitId}/news/`
   - `/d2l/api/le/1.47/{orgUnitId}/dropbox/folders/`
3. Saves raw JSON under `data/raw/{orgUnitId}/`
4. Downloads PDF attachments with the same browser request context; if that fails, scrapes `a[href]` PDF links from the course home as a DOM fallback
5. Upserts Courses / Assignments / Announcements / Documents into SQLite (idempotent)

Fixture ingest (`ingest --fixtures` / `demo`) never launches Chromium.

If `storage_state.json` is missing:

```text
Missing .../storage_state.json. Run `uv run school-secretary login` first (NetID, password, Duo).
```

SSO session capture on your WSL machine is the intended path; this repo only stores cookies locally.

---

## Google Calendar

```bash
uv run school-secretary calendar-sync
```

Without `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN`, this writes `data/calendar/school-secretary.ics` (assignment + sub-task deadlines). Import that file into Google Calendar, or fill the three OAuth keys to push via the Calendar API.

---

## WhatsApp (Telegram stays primary)

Same commands as Telegram: `/start` `/ask` `/briefing` `/evening` `/plan` `/scaffold` `/email` `/study` `/habits` `/calendar`.

```bash
uv run school-secretary whatsapp
```

No Meta credentials: mock webhook on **http://127.0.0.1:43148**.

```bash
curl -sS http://127.0.0.1:43148/health
curl -sS -X POST http://127.0.0.1:43148/mock/message \
  -H 'content-type: application/json' \
  -d '{"text":"/briefing"}'
```

With Cloud API keys, point Meta’s webhook at `/webhook` on `WHATSAPP_WEBHOOK_PORT` (default 43148).

---

## MCP (Cursor)

Uncommon HTTP port **43147** (override with `MCP_HOST` / `MCP_PORT`).

```bash
uv run school-secretary mcp
```

For Cursor stdio config:

```bash
uv run school-secretary mcp --stdio
```

Example `mcp.json` snippet:

```json
{
  "mcpServers": {
    "school-secretary": {
      "command": "uv",
      "args": ["run", "school-secretary", "mcp", "--stdio"]
    }
  }
}
```

Tools: `list_courses`, `list_assignments`, `list_announcements`, `query_syllabus`, `get_plan`, `list_subtasks`, `scaffold_assignment`, `get_briefing`, `record_study`, `list_habits`, `draft_professor_email`, `db_status`, `sync_deadlines`.

## Layout

```
src/school_secretary/
  ingest/     Playwright session, Brightspace LE client, fixture seed, JSON/HTML parser
  db/         SQLAlchemy SQLite (courses, assignments, announcements, documents, habits, subtasks)
  rag/        PyMuPDF → hashing embeddings → ChromaDB; LlamaIndex retriever + extractive QA
  agents/     triage, planner, drafting, study-habit memory, orchestrator
  mcp_app/    MCP HTTP/stdio server (`mcp` 2.x `MCPServer`)
  telegram_app/
  whatsapp_app/
  calendar_sync.py
data/fixtures/pdfs/   generated on ingest
data/raw/             gitignored raw JSON + PDFs (from ingest --live or fixtures)
data/calendar/        gitignored ICS
data/browser/         Playwright Chromium profile — never commit (cookies/cache)
storage_state.json    gitignored Playwright cookies (session.json is a copy)
data/secretary.db     local SQLite; gitignored by default — do not commit Chromium `data/browser/`
```

## Academic integrity

Drafting never writes essay prose or working algorithms. Scaffolds always include an integrity header, `# TODO` comments, `NotImplementedError`, and skipped pytest shells.
