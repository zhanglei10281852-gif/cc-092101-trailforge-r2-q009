from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRAILFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_title: str = "TrailForge API"
    api_version: str = "0.1.0"
    database_url: str = "sqlite:///./data/trailforge.db"
    sqlite_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    sqlite_busy_retries: int = Field(default=5, ge=0, le=20)
    sqlite_busy_backoff_seconds: float = Field(default=0.05, ge=0.0, le=2.0)
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=500)

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite"):
            raise ValueError("TrailForge supports SQLite database URLs only")
        return value

    @property
    def database_path(self) -> Path | None:
        if self.database_url in {"sqlite://", "sqlite:///:memory:"}:
            return None
        marker = "sqlite:///"
        if not self.database_url.startswith(marker):
            return None
        raw = self.database_url[len(marker) :]
        return Path(raw).expanduser().resolve()

    def ensure_runtime_directories(self) -> None:
        path = self.database_path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
