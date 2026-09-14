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
3. Answers **“What is the late penalty for CISC 235?”** from the syllabus PDF (10% per day, 3-day cap)
4. Prints a Lab 2 plan with micro-deadlines
5. Writes a BST scaffold under `data/scaffolds/` (stubs only)
6. Prints a morning briefing (announcements, due dates, study-habit summary)

Useful follow-ups:

```bash
uv run school-secretary query "What is the late penalty for CISC 235?"
uv run school-secretary plan "Lab 2"
uv run school-secretary scaffold "Essay 1"
uv run school-secretary briefing morning
uv run school-secretary briefing evening
uv run school-secretary study --course "CISC 235" --minutes 45 --notes "BST traces"
uv run school-secretary email --topic "clarification on Lab 2 test cases" --course "CISC 235"
uv run pytest
```

Re-running `demo` or `ingest --fixtures` is idempotent (same D2L ids upsert).

## Environment files

| File | Where | Purpose |
| --- | --- | --- |
| `.env.example` | repository root (committed) | Template with every variable this repo reads |
| `.env` | repository root (gitignored) | Your real secrets. Create with `cp .env.example .env` |

Variable names (exact):

| Name | Required? | Used by |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | only for `school-secretary telegram` | python-telegram-bot |
| `TELEGRAM_CHAT_ID` | optional | proactive 8:00 / 20:00 briefings if you never sent `/start` |
| `OPENAI_API_KEY` | optional | function calling + richer RAG synthesis |
| `OPENAI_MODEL` | optional, default `gpt-4o-mini` | OpenAI model id |
| `ONQ_BASE_URL` | optional, default `https://onq.queensu.ca` | live ingest |
| `MCP_HOST` / `MCP_PORT` | optional, default `127.0.0.1` / `43147` | MCP HTTP bind |

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
TELEGRAM_BOT_TOKEN=123456789:AAHyourTokenHere
```

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

Commands: `/start` `/ask` `/briefing` `/evening` `/plan` `/scaffold` `/email` `/study` plus free-text questions.

---

## 2. Playwright Queen’s SSO (one-time manual login)

Live onQ ingest is **optional**. The fixture demo never opens a browser.

**Install Chromium once** (Playwright’s browser, not your system Chrome):

```bash
uv sync
uv run playwright install chromium
```

**Exact one-time login command** (run on a machine with a display — your laptop, not a headless server):

```bash
uv run school-secretary login
```

**What you should see**

1. A **Chromium** window opens (Playwright persistent profile, 1280×900).
2. It navigates to **`https://onq.queensu.ca/d2l/home`**.
3. Queen’s NetID SSO appears (username / password / MFA as your account requires).
4. After a successful login you should see the **onQ / Brightspace homepage** (course tiles and the D2L navbar).
5. Leave that window as-is, switch back to the terminal, and **press Enter**.

**Where cookies are saved**

| Path | What |
| --- | --- |
| `session.json` | Playwright `storage_state` (cookies + origins), **repository root**, gitignored |
| `data/browser/` | Persistent Chromium user-data dir for the same profile |

The login command prints both paths when it finishes.

**Silent / headless refresh after that**

```bash
uv run school-secretary ingest --live
```

This does **not** open a window. It:

1. Loads cookies from `session.json`
2. Calls Brightspace LE APIs with those cookies:
   - `/d2l/api/lp/1.47/enrollments/myenrollments/`
   - `/d2l/api/le/1.47/{orgUnitId}/news/`
   - `/d2l/api/le/1.47/{orgUnitId}/dropbox/folders/`
3. Saves raw JSON under `data/raw/{orgUnitId}/`
4. Downloads PDF attachments over HTTP; if that fails, a **headless** Chromium context (same profile + `session.json`) scrapes `a[href]` PDF links as a DOM fallback
5. Upserts Courses / Assignments / Announcements / Documents into SQLite (idempotent)

If `session.json` is missing:

```text
Missing .../session.json. Run `uv run school-secretary login` first.
```

SSO session capture on a personal machine is the intended path; this repo only stores cookies locally.

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

Tools: `list_courses`, `list_assignments`, `list_announcements`, `query_syllabus`, `get_plan`, `scaffold_assignment`, `get_briefing`, `record_study`, `draft_professor_email`.

## Layout

```
src/school_secretary/
  ingest/     Playwright session, Brightspace LE client, fixture seed, JSON/HTML parser
  db/         SQLAlchemy SQLite (courses, assignments, announcements, documents, habits, subtasks)
  rag/        PyMuPDF → hashing embeddings → ChromaDB; LlamaIndex retriever + extractive QA
  agents/     triage, planner, drafting, study-habit memory, orchestrator
  mcp_app/    FastMCP server
  telegram_app/
data/fixtures/pdfs/   generated on ingest
data/raw/             gitignored raw JSON + PDFs
session.json          gitignored cookies
```

## Academic integrity

Drafting never writes essay prose or working algorithms. Scaffolds always include an integrity header, `# TODO` comments, `NotImplementedError`, and skipped pytest shells.
