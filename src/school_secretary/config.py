from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


ROOT = repo_root()


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
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    onq_base_url: str = "https://onq.queensu.ca"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 43147
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
        return ROOT / "session.json"

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
        return self.data_dir / "secretary.db"

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

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.raw_dir,
            self.chroma_path,
            self.browser_profile_dir,
            self.scaffolds_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
