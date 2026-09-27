"""Typed configuration for the investigation service."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration, read from ``RECONAI_AGENT_``-prefixed variables.

    Every value has a default that works for local development, so the service
    starts with no environment set at all. There are no secrets here: this phase
    has no external credentials, no database and no model provider.
    """

    model_config = SettingsConfigDict(
        env_prefix="RECONAI_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "reconai-investigation-service"
    environment: Environment = "local"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: LogLevel = "INFO"
