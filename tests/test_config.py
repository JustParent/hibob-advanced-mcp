"""Configuration and host-parsing tests."""

from __future__ import annotations

import pytest

from hibob_advanced_mcp import config
from hibob_advanced_mcp.config import (
    ENV_API_HOST,
    ENV_READ_ONLY,
    ENV_SERVICE_USER_ID,
    hibob_api_base,
    load_settings,
    parse_hibob_api_host,
    read_only_enabled,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("   ", ""),
        ("api.sandbox.hibob.com", "api.sandbox.hibob.com"),
        ("https://api.sandbox.hibob.com/v1", "api.sandbox.hibob.com"),
        ("http://api.hibob.com", "api.hibob.com"),
        ("//api.sandbox.hibob.com", "api.sandbox.hibob.com"),
        ("api.hibob.com:443/v1", "api.hibob.com"),
        ("api.hibob.com/v1/people", "api.hibob.com"),
    ],
)
def test_parse_hibob_api_host(raw: str, expected: str) -> None:
    assert parse_hibob_api_host(raw) == expected


def test_api_base_defaults_to_production() -> None:
    assert hibob_api_base("") == "https://api.hibob.com/v1"


def test_api_base_uses_sandbox_when_configured() -> None:
    assert (
        hibob_api_base("https://api.sandbox.hibob.com/v1")
        == "https://api.sandbox.hibob.com/v1"
    )


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " True "])
def test_read_only_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_READ_ONLY, value)
    assert read_only_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_read_only_falsy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_READ_ONLY, value)
    assert read_only_enabled() is False


def test_settings_report_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_SERVICE_USER_ID, raising=False)
    assert load_settings().credentials_configured is False


def test_settings_read_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_settings().api_base == "https://api.hibob.com/v1"
    monkeypatch.setenv(ENV_API_HOST, "api.sandbox.hibob.com")
    assert load_settings().api_base == "https://api.sandbox.hibob.com/v1"


def test_non_hibob_host_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(config, "_warned_hosts", set())
    monkeypatch.setenv(ENV_API_HOST, "evil.example.com")
    settings = load_settings()
    assert settings.api_base == "https://evil.example.com/v1"
    assert "not a *.hibob.com host" in capsys.readouterr().err


def test_non_hibob_host_warns_only_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Settings are read repeatedly; the warning must not spam stderr."""
    monkeypatch.setattr(config, "_warned_hosts", set())
    monkeypatch.setenv(ENV_API_HOST, "evil.example.com")
    load_settings()
    capsys.readouterr()
    load_settings()
    assert capsys.readouterr().err == ""


def test_hibob_hosts_do_not_warn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(config, "_warned_hosts", set())
    monkeypatch.setenv(ENV_API_HOST, "api.sandbox.hibob.com")
    load_settings()
    assert capsys.readouterr().err == ""
