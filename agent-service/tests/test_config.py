"""Tests for typed configuration loading."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_are_usable_without_any_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SERVICE_NAME", "ENVIRONMENT", "HOST", "PORT", "LOG_LEVEL"):
        monkeypatch.delenv(f"RECONAI_AGENT_{name}", raising=False)

    settings = Settings(_env_file=None)

    assert settings.service_name == "reconai-investigation-service"
    assert settings.environment == "local"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.log_level == "INFO"


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONAI_AGENT_ENVIRONMENT", "dev")
    monkeypatch.setenv("RECONAI_AGENT_HOST", "127.0.0.1")
    monkeypatch.setenv("RECONAI_AGENT_PORT", "9100")
    monkeypatch.setenv("RECONAI_AGENT_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.environment == "dev"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9100
    assert settings.log_level == "DEBUG"


def test_the_prefix_is_required_so_unrelated_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECONAI_AGENT_PORT", raising=False)
    monkeypatch.setenv("PORT", "1234")

    assert Settings(_env_file=None).port == 8000


def test_an_unusable_port_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, port=70000)


def test_an_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="CHATTY")


def test_an_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="staging-ish")


def test_the_only_credential_has_no_default_and_is_a_secret() -> None:
    """The model API key is the one secret this service takes.

    It must have no default — a hardcoded key is the failure this guards — and
    must be a SecretStr so an accidental repr or log of settings cannot print it.
    """
    suspicious = ("password", "secret", "token", "api_key", "apikey", "credential")
    credentials = [
        name for name in Settings.model_fields if any(word in name for word in suspicious)
    ]

    assert credentials == ["llm_api_key"]
    assert Settings(_env_file=None).llm_api_key is None


def test_a_configured_api_key_is_not_printable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONAI_AGENT_LLM_API_KEY", "sk-do-not-print-me")

    settings = Settings(_env_file=None)

    assert "do-not-print-me" not in repr(settings)
    assert "do-not-print-me" not in str(settings.llm_api_key)
    assert settings.llm_api_key.get_secret_value() == "sk-do-not-print-me"


def test_no_model_is_configured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service runs with no provider at all; only investigation is unavailable."""
    for name in ("LLM_PROVIDER", "LLM_API_KEY"):
        monkeypatch.delenv(f"RECONAI_AGENT_{name}", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "none"
    assert settings.investigation_model_configured is False


def test_a_provider_needs_both_a_name_and_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONAI_AGENT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("RECONAI_AGENT_LLM_API_KEY", raising=False)
    assert Settings(_env_file=None).investigation_model_configured is False

    monkeypatch.setenv("RECONAI_AGENT_LLM_API_KEY", "sk-test")
    assert Settings(_env_file=None).investigation_model_configured is True


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="mystery-corp")


def test_the_tool_round_bound_is_constrained() -> None:
    """An unbounded agent loop is an unbounded bill."""
    assert Settings(_env_file=None).investigation_max_tool_rounds == 8

    with pytest.raises(ValidationError):
        Settings(_env_file=None, investigation_max_tool_rounds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, investigation_max_tool_rounds=500)


# ---------------------------------------------------------------------------
# Kafka configuration
# ---------------------------------------------------------------------------


def test_kafka_defaults_target_the_local_compose_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_EXCEPTIONS_TOPIC",
        "KAFKA_CONSUMER_GROUP",
    ):
        monkeypatch.delenv(f"RECONAI_AGENT_{name}", raising=False)

    settings = Settings(_env_file=None)

    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.kafka_exceptions_topic == "reconciliation.exceptions"
    assert settings.kafka_consumer_group == "reconai-investigation-service"


def test_the_topic_default_matches_what_the_financial_core_publishes_to() -> None:
    """The producer's default is reconciliation.exceptions; these must agree."""
    assert Settings(_env_file=None).kafka_exceptions_topic == "reconciliation.exceptions"


def test_kafka_settings_can_be_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECONAI_AGENT_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("RECONAI_AGENT_KAFKA_EXCEPTIONS_TOPIC", "other.topic")
    monkeypatch.setenv("RECONAI_AGENT_KAFKA_CONSUMER_GROUP", "other-group")

    settings = Settings(_env_file=None)

    assert settings.kafka_bootstrap_servers == "kafka:9092"
    assert settings.kafka_exceptions_topic == "other.topic"
    assert settings.kafka_consumer_group == "other-group"


def test_the_consumer_group_is_fixed_rather_than_generated() -> None:
    """A generated group id would replay or duplicate work on every restart."""
    assert Settings(_env_file=None).kafka_consumer_group == Settings(_env_file=None).kafka_consumer_group


# ---------------------------------------------------------------------------
# Financial Core configuration
# ---------------------------------------------------------------------------


def test_financial_core_defaults_target_the_local_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("FINANCIAL_CORE_BASE_URL", "FINANCIAL_CORE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(f"RECONAI_AGENT_{name}", raising=False)

    settings = Settings(_env_file=None)

    assert settings.financial_core_base_url == "http://localhost:8099"
    assert settings.financial_core_timeout_seconds == 5.0


def test_the_financial_core_base_url_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container cannot reach the host's localhost; it needs a different address."""
    monkeypatch.setenv("RECONAI_AGENT_FINANCIAL_CORE_BASE_URL", "http://host.docker.internal:8099")
    monkeypatch.setenv("RECONAI_AGENT_FINANCIAL_CORE_TIMEOUT_SECONDS", "2.5")

    settings = Settings(_env_file=None)

    assert settings.financial_core_base_url == "http://host.docker.internal:8099"
    assert settings.financial_core_timeout_seconds == 2.5


def test_an_unusable_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, financial_core_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, financial_core_timeout_seconds=-1)
