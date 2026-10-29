"""Server-only configuration for the real-data application."""

import ipaddress
import os
import tempfile
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMode(StrEnum):
    LIVE = "LIVE"


def is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_ollama_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        valid = (
            parsed.scheme in {"http", "https"}
            and is_loopback_host(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Ollama must use a local loopback HTTP URL.")
    return value.strip().rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_env: str = "development"
    app_mode: AppMode = AppMode.LIVE
    artifact_storage_root: Path = Field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "astra-public-cache"
    )
    stock_model_timeout_seconds: float = Field(default=90, gt=0, le=180)
    internal_api_token: str | None = Field(default=None, repr=False)
    snapshot_encryption_key: str | None = Field(default=None, repr=False)
    snapshot_ttl_seconds: int = Field(default=14400, ge=300, le=86400)
    model_router_provider: str = "none"
    groq_api_key: str | None = Field(default=None, repr=False)
    groq_model_id: str = Field(default="openai/gpt-oss-120b", min_length=1, max_length=160)
    model_research_director_id: str | None = None
    model_analyst_id: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model_id: str = Field(default="llama3.2", min_length=1, max_length=160)
    model_timeout_seconds: float = Field(default=25, ge=1, le=60)
    model_max_output_tokens: int = Field(default=1500, ge=64, le=4000)
    model_context_max_chars: int = Field(default=24000, ge=2000, le=64000)
    yahoo_cache_ttl_seconds: int = Field(default=1800, ge=60, le=86400)

    @field_validator("ollama_base_url")
    @classmethod
    def local_ollama(cls, value: str) -> str:
        return validate_ollama_url(value)

    @field_validator("model_router_provider")
    @classmethod
    def provider_supported(cls, value: str) -> str:
        if value not in {"none", "groq", "ollama"}:
            raise ValueError("Choose data-only, Groq, or local Ollama.")
        return value

    @property
    def model_ready(self) -> bool:
        return (
            self.model_router_provider == "none"
            or (self.model_router_provider == "groq" and bool(self.groq_api_key))
            or (self.model_router_provider == "ollama" and bool(self.ollama_model_id))
        )

    @property
    def live_ready(self) -> bool:
        return True

    @property
    def cloud(self) -> bool:
        return bool(os.environ.get("VERCEL")) or self.app_env == "production"


def get_settings() -> Settings:
    # Hosted deployments read only environment variables, never a saved browser preference file.
    return Settings(_env_file=None if os.environ.get("VERCEL") else ".env")
