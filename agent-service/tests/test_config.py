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


def test_configuration_holds_no_credentials() -> None:
    """This phase talks to nothing, so nothing here should look like a secret."""
    suspicious = ("password", "secret", "token", "api_key", "apikey", "credential")
    names = set(Settings.model_fields)

    assert not [name for name in names if any(word in name for word in suspicious)]
