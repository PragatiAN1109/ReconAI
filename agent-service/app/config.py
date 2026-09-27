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

    # Kafka. The default suits running this service on the host against the
    # Compose broker. Inside a container "localhost" would mean the container
    # itself, so a containerised deployment must set this to the broker's
    # reachable address.
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_exceptions_topic: str = "reconciliation.exceptions"
    # Fixed, never generated: a stable group keeps committed offsets across
    # restarts and lets several instances share partitions instead of each one
    # receiving every event.
    kafka_consumer_group: str = "reconai-investigation-service"
