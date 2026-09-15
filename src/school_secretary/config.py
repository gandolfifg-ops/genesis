from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


ROOT = repo_root()
load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    onq_base_url: str = "https://onq.queensu.ca"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 43147
    google_calendar_id: str = "primary"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_webhook_host: str = "127.0.0.1"
    whatsapp_webhook_port: int = 43148
    school_secretary_data_dir: Path = ROOT / "data"
    school_secretary_session_path: Path = ROOT / "storage_state.json"
    timezone: str = "America/Toronto"

    @property
    def data_dir(self) -> Path:
        return Path(self.school_secretary_data_dir)

    @property
    def storage_state_path(self) -> Path:
        return Path(self.school_secretary_session_path)

    @property
    def session_json_copy_path(self) -> Path:
        return Path(self.school_secretary_session_path).expanduser().parent / "session.json"

    @property
    def session_path(self) -> Path:
        """Playwright storage state. Prefers storage_state.json, then a legacy session.json copy."""
        primary = self.storage_state_path
        if primary.exists():
            return primary
        legacy = self.session_json_copy_path
        if legacy.exists():
            return legacy
        return primary

    @property
    def database_path(self) -> Path:
        target = self.data_dir / "secretary.db"
        if not target.exists():
            import shutil
            bundled = ROOT / "src" / "school_secretary" / "data" / "secretary.db"
            if bundled.exists():
                self.data_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundled, target)
        return target

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def fixtures_dir(self) -> Path:
        return ROOT / "data" / "fixtures"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser"

    @property
    def scaffolds_dir(self) -> Path:
        return self.data_dir / "scaffolds"

    @property
    def telegram_chat_id_path(self) -> Path:
        return self.data_dir / "telegram_chat_id.txt"

    @property
    def calendar_dir(self) -> Path:
        return self.data_dir / "calendar"

    @property
    def whatsapp_outbox_path(self) -> Path:
        return self.data_dir / "whatsapp_outbox.jsonl"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.raw_dir,
            self.chroma_path,
            self.browser_profile_dir,
            self.scaffolds_dir,
            self.calendar_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
