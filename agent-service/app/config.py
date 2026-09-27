"""Typed configuration for the investigation service."""

from pathlib import Path
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

    # PostgreSQL. The async driver is part of the URL, so this must be an
    # asyncpg URL rather than the psycopg one the financial core uses.
    #
    # The port is 55432, not 5432: the Compose stack publishes PostgreSQL there
    # on machines that already run a local server on the default port. Keep this
    # aligned with RECONAI_POSTGRES_PORT.
    #
    # Same database as the financial core, different schema. Shared storage is
    # not shared ownership: this service reads and writes only "investigation".
    database_url: str = "postgresql+asyncpg://reconai:reconai@localhost:55432/reconai"

    # Financial Core. Read-only evidence retrieval only; this service never
    # writes to it.
    #
    # The default is the port the backend README uses for local runs alongside
    # an occupied 8080. Inside a container "localhost" would mean the container
    # itself, so a containerised deployment must point this at the reachable
    # address (host.docker.internal, or a Compose service name).
    financial_core_base_url: str = "http://localhost:8099"
    # Evidence retrieval happens inside an investigation, not on a request path,
    # so a few seconds of patience is fine — but an unbounded wait is not.
    financial_core_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # Policy corpus. Synthetic Markdown documents searched lexically; see
    # policies/README.md. Resolved relative to the repository root so the
    # service works from a checkout without configuration, and overridable so
    # tests can point at a fixture corpus.
    policy_corpus_path: Path = Path(__file__).resolve().parent.parent.parent / "policies"

    @property
    def redacted_database_url(self) -> str:
        """The database URL with any password removed, safe to log."""
        if "@" not in self.database_url:
            return self.database_url
        scheme_and_credentials, _, host_and_path = self.database_url.partition("@")
        scheme, _, credentials = scheme_and_credentials.partition("://")
        user = credentials.partition(":")[0]
        return f"{scheme}://{user}:***@{host_and_path}"
