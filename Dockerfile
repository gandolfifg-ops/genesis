# Telegram daemon + scheduled ingest --raw. Live headed onQ login stays on WSL/desktop.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    SCHOOL_SECRETARY_DATA_DIR=/app/data \
    SCHOOL_SECRETARY_SESSION_PATH=/app/storage_state.json

WORKDIR /app

COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src

RUN uv sync --frozen --no-dev \
    && mkdir -p /app/data

# Chromium/Playwright browsers are NOT installed. This image runs Telegram and ingest --raw only.
CMD ["uv", "run", "school-secretary", "telegram"]
