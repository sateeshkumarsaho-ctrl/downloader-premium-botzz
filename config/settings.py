from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    api_id: int = Field(alias="API_ID")
    api_hash: str = Field(alias="API_HASH")

    session_secret: str | None = Field(default=None, alias="SESSION_SECRET")
    admin_chat_id: int | None = Field(default=None, alias="ADMIN_CHAT_ID")
    pwthor_base_url: str = Field(default="https://pwthor.live", alias="PWTHOR_BASE_URL")
    allowed_source_hosts: str = Field(default="*", alias="ALLOWED_SOURCE_HOSTS")
    allowed_media_hosts: str = Field(default="*", alias="ALLOWED_MEDIA_HOSTS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    downloads_dir: Path = Field(default=Path("downloads"), alias="DOWNLOADS_DIR")
    sessions_dir: Path = Field(default=Path("sessions"), alias="SESSIONS_DIR")

    max_download_mb: int = Field(default=450, alias="MAX_DOWNLOAD_MB")
    request_timeout_seconds: int = Field(default=30, alias="REQUEST_TIMEOUT_SECONDS")
    download_timeout_seconds: int = Field(default=1800, alias="DOWNLOAD_TIMEOUT_SECONDS")
    progress_update_seconds: int = Field(default=10, alias="PROGRESS_UPDATE_SECONDS")
    cleanup_interval_seconds: int = Field(default=600, alias="CLEANUP_INTERVAL_SECONDS")
    session_ttl_days: int = Field(default=30, alias="SESSION_TTL_DAYS")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")

    max_global_downloads: int = Field(default=1, alias="MAX_GLOBAL_DOWNLOADS")
    per_user_queue_size: int = Field(default=3, alias="PER_USER_QUEUE_SIZE")
    rate_limit_messages: int = Field(default=8, alias="RATE_LIMIT_MESSAGES")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024

    @property
    def allowed_media_host_set(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.allowed_media_hosts.split(",")
            if host.strip()
        }

    @property
    def allowed_source_host_set(self) -> set[str]:
        return {
            host.strip().lower()
            for host in self.allowed_source_hosts.split(",")
            if host.strip()
        }

    @field_validator("pwthor_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if value != "https://pwthor.live" and not value.endswith(".pwthor.live"):
            raise ValueError("PWTHOR_BASE_URL must point to pwthor.live")
        return value

    @field_validator("max_download_mb")
    @classmethod
    def validate_download_size(cls, value: int) -> int:
        if not 1 <= value <= 1500:
            raise ValueError("MAX_DOWNLOAD_MB must be between 1 and 1500")
        return value

    @field_validator("allowed_media_hosts")
    @classmethod
    def validate_allowed_media_hosts(cls, value: str) -> str:
        return cls._validate_host_list(value, "ALLOWED_MEDIA_HOSTS", allow_wildcard=True)

    @field_validator("allowed_source_hosts")
    @classmethod
    def validate_allowed_source_hosts(cls, value: str) -> str:
        return cls._validate_host_list(value, "ALLOWED_SOURCE_HOSTS", allow_wildcard=True)

    @classmethod
    def _validate_host_list(cls, value: str, name: str, allow_wildcard: bool) -> str:
        hosts = [host.strip().lower() for host in value.split(",") if host.strip()]
        if not hosts:
            raise ValueError(f"{name} must contain at least one host")
        for host in hosts:
            if allow_wildcard and host == "*":
                continue
            if not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError(f"{name} must be a comma-separated host list")
        return ",".join(hosts)
