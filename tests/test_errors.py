"""Error translation tests: every failure must say what to do about it."""

from __future__ import annotations

import httpx
import pytest

from hibob_advanced_mcp.errors import (
    MANAGE_POSITIONS_PERMISSION_PATH,
    HiBobApiError,
    format_exception,
    raise_for_hibob_error,
)


def _response(
    status: int, json: dict | None = None, headers: dict | None = None, text: str = ""
) -> httpx.Response:
    request = httpx.Request("GET", "https://api.hibob.com/v1/workforce-planning/positions/42")
    if json is not None:
        return httpx.Response(status, json=json, headers=headers, request=request)
    return httpx.Response(status, text=text, headers=headers, request=request)


def test_success_does_not_raise() -> None:
    raise_for_hibob_error(_response(200, {"ok": True}))


def test_401_names_both_credential_env_vars() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(401, {"error": "unauthorized"}))
    message = str(excinfo.value)
    assert "HIBOB_SERVICE_USER_ID" in message
    assert "HIBOB_SERVICE_USER_TOKEN" in message


def test_403_names_the_exact_permission_path() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(403, {"error": "forbidden"}))
    assert MANAGE_POSITIONS_PERMISSION_PATH in str(excinfo.value)


def test_404_points_at_the_search_tools() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(404, {"error": "not found"}))
    message = str(excinfo.value)
    assert "hibob_search_positions" in message
    assert "/workforce-planning/positions/42" in message


def test_429_reports_retry_after_and_limits() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(429, {}, headers={"Retry-After": "30"}))
    message = str(excinfo.value)
    assert "30" in message
    assert "10/min" in message


def test_400_passes_through_hibob_validation_detail() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(
            _response(400, {"key": "missing_field", "error": "fte is required"})
        )
    assert "fte is required" in str(excinfo.value)
    assert excinfo.value.hibob_key == "missing_field"


def test_500_is_described_as_transient() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(500, {}))
    assert "transient" in str(excinfo.value)


def test_non_json_error_body_is_tolerated() -> None:
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(_response(502, text="<html>bad gateway</html>"))
    assert excinfo.value.status_code == 502


def test_response_without_request_is_tolerated() -> None:
    """A detached response must not turn into a confusing RuntimeError."""
    with pytest.raises(HiBobApiError) as excinfo:
        raise_for_hibob_error(httpx.Response(404, json={"error": "gone"}))
    assert excinfo.value.status_code == 404


def test_format_exception_renders_timeouts_without_traceback() -> None:
    assert "timed out" in format_exception(httpx.TimeoutException("slow"))


def test_format_exception_prefixes_value_errors() -> None:
    assert format_exception(ValueError("bad field")) == "Error: bad field"
