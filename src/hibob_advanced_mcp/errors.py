"""HiBob API error handling.

HiBob returns errors as ``{"key": ..., "error": ...}``. These are translated
into messages that tell the caller what to change, following the convention of
naming the exact HiBob permission path when access is denied.
"""

from __future__ import annotations

import httpx

from .config import ENV_SERVICE_USER_ID, ENV_SERVICE_USER_TOKEN

MANAGE_POSITIONS_PERMISSION_PATH = (
    "Features > Workforce planning > Position management > Manage positions"
)

RATE_LIMITS_SUMMARY = (
    "position/opening/budget writes: 10/min, searches: 100/min, metadata: 50/min"
)


class HiBobConfigError(Exception):
    """Raised when the server is not configured to reach HiBob."""


class HiBobApiError(Exception):
    """A HiBob API call failed. ``str(err)`` is safe to show to a caller."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        hibob_key: str | None = None,
        hibob_error: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.hibob_key = hibob_key
        self.hibob_error = hibob_error


def _parse_error_body(response: httpx.Response) -> tuple[str | None, str | None]:
    """Pull ``key``/``error`` out of a HiBob error body, tolerating non-JSON."""
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return None, text[:500] or None
    if isinstance(body, dict):
        key = body.get("key")
        error = body.get("error") or body.get("message")
        return (
            key if isinstance(key, str) else None,
            error if isinstance(error, str) else None,
        )
    return None, None


def _retry_after_seconds(response: httpx.Response) -> str:
    value = response.headers.get("Retry-After", "").strip()
    return value or "a few"


def raise_for_hibob_error(response: httpx.Response) -> None:
    """Raise :class:`HiBobApiError` with an actionable message on HTTP errors."""
    if not response.is_error:
        return

    status = response.status_code
    key, detail = _parse_error_body(response)
    try:
        path = response.request.url.path
    except RuntimeError:  # response built without an originating request
        path = ""

    if status == 401:
        message = (
            "HiBob rejected the credentials (401). Check that "
            f"{ENV_SERVICE_USER_ID} and {ENV_SERVICE_USER_TOKEN} hold the service "
            "user's ID and token, and that the service user is still active in "
            "HiBob (Settings > Integrations > Service users)."
        )
    elif status == 403:
        message = (
            "HiBob denied access (403). Grant the service user's permission group: "
            f"{MANAGE_POSITIONS_PERMISSION_PATH}. If your HiBob account restricts "
            "API access by IP, also allow this server's outbound IP address."
        )
    elif status == 404:
        message = (
            f"HiBob returned 404 for {path}. Check the position, opening, or budget "
            "ID - use hibob_search_positions or hibob_search_position_openings to "
            "look up current IDs."
        )
    elif status == 429:
        message = (
            f"HiBob rate limit exceeded (429). Wait {_retry_after_seconds(response)} "
            f"seconds before retrying. Limits are {RATE_LIMITS_SUMMARY}."
        )
    elif status == 400:
        message = (
            "HiBob rejected the request as invalid (400)"
            + (f": {detail}" if detail else ".")
            + " Use hibob_list_workforce_fields to confirm field IDs and required "
            "values, and hibob_get_company_named_lists to resolve list item IDs."
        )
    elif status >= 500:
        message = (
            f"HiBob returned a server error ({status}). This is usually transient - "
            "retry shortly."
        )
    else:
        message = f"HiBob request failed with status {status}" + (
            f": {detail}" if detail else "."
        )

    if detail and status not in (400,) and detail not in message:
        message = f"{message} HiBob said: {detail}"

    raise HiBobApiError(message, status_code=status, hibob_key=key, hibob_error=detail)


def format_exception(exc: Exception) -> str:
    """Render an exception as a caller-facing message, never a traceback."""
    if isinstance(exc, (HiBobApiError, HiBobConfigError)):
        return f"Error: {exc}"
    if isinstance(exc, ValueError):
        return f"Error: {exc}"
    if isinstance(exc, httpx.TimeoutException):
        return "Error: The request to HiBob timed out. Please try again."
    if isinstance(exc, httpx.HTTPError):
        return f"Error: Could not reach the HiBob API: {exc}"
    return f"Error: Unexpected {type(exc).__name__}: {exc}"
