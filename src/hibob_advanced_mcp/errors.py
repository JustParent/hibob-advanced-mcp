"""HiBob API error handling.

HiBob returns errors as ``{"key": ..., "error": ...}``, some as
``{"errorMessage": ...}``, and a rejected field under ``{"errors": {field ID:
...}}``. These are translated into messages that tell the caller what to
change, following the convention of naming the exact HiBob permission path
when access is denied.
"""

from __future__ import annotations

from urllib.parse import unquote

import httpx

from .config import ENV_SERVICE_USER_ID, ENV_SERVICE_USER_TOKEN

MANAGE_POSITIONS_PERMISSION_PATH = (
    "Features > Workforce planning > Position management > Manage positions"
)
BUDGET_PERMISSION_PATH = (
    "Features > Workforce planning > Position management > Position budget settings"
)

TOTAL_COST_FIELD = "/positionBudget/totalPositionCostCurrencyValue"
# HiBob's UI calculates the total position cost; its API does not, and some
# accounts make the field mandatory.
TOTAL_COST_NOTE = (
    "HiBob works out the total position cost from multipliers in its own "
    "configuration, which its API cannot read. A figure can be inferred by "
    "inspecting other positions' budgets, though which multiplier applies can "
    "depend on more than the position's site; only HiBob's configuration is "
    "authoritative. Ask the user for the figure, and never submit an inferred "
    "one without their approval."
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
        error = body.get("error") or body.get("message") or body.get("errorMessage")
        errors = body.get("errors")
        if not isinstance(error, str) and isinstance(errors, dict):
            # HiBob names each field it rejects, keyed by field ID:
            # {"errors": {"/positionBudget/...": {"error": "MISSING_MANDATORY_FIELD",
            # "message": "Missing mandatory field '/positionBudget/...'"}}}
            entries = [entry for entry in errors.values() if isinstance(entry, dict)]
            error = (
                "; ".join(
                    str(entry.get("message") or entry.get("error"))
                    for entry in entries
                    if entry.get("message") or entry.get("error")
                )
                or None
            )
            if key is None and entries:
                key = entries[0].get("error")
        return (
            key if isinstance(key, str) else None,
            error if isinstance(error, str) else None,
        )
    return None, None


NAMED_LISTS_PATH_PREFIX = "/company/named-lists/"


def _named_list_from_path(path: str) -> str | None:
    """The list name in a single-named-list request path, if that is what it is."""
    _, marker, rest = path.partition(NAMED_LISTS_PATH_PREFIX)
    if not marker:
        return None
    name = unquote(rest.split("/", 1)[0]).strip()
    return name or None


def _permission_for(path: str) -> str:
    """The permission a request needs, as the path to grant it in HiBob.

    Budget writes need their own permission; budget searches do not.
    """
    if "/workforce-planning/" in path and "/position-budget" in path:
        return BUDGET_PERMISSION_PATH
    return MANAGE_POSITIONS_PERMISSION_PATH


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

    if status == 401 and detail and "permission" in detail.lower():
        # HiBob refuses some writes the service user may not make with a 401
        # rather than a 403: {"errorMessage": "No permissions to position budget"}.
        message = (
            "HiBob denied the service user permission (401). Grant the service "
            f"user's permission group: {_permission_for(path)}."
        )
    elif status == 401:
        message = (
            "HiBob rejected the credentials (401). Check that "
            f"{ENV_SERVICE_USER_ID} and {ENV_SERVICE_USER_TOKEN} hold the service "
            "user's ID and token, and that the service user is still active in "
            "HiBob (Settings > Integrations > Service users)."
        )
    elif status == 403:
        message = (
            "HiBob denied access (403). Grant the service user's permission group: "
            f"{_permission_for(path)}. If your HiBob account restricts "
            "API access by IP, also allow this server's outbound IP address."
        )
    elif status == 404 and (list_name := _named_list_from_path(path)):
        message = (
            f"HiBob has no named list called {list_name!r} (404). Call "
            "hibob_get_company_named_lists without list_name to see the "
            "available list names."
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
    elif (
        status == 400
        and detail
        and key == "MISSING_MANDATORY_FIELD"
        and TOTAL_COST_FIELD in detail
    ):
        message = (
            f"HiBob rejected the request as invalid (400): {detail.rstrip('.')}. "
            "This HiBob account requires the total position cost on a budget, "
            f"and nothing was written. {TOTAL_COST_NOTE}"
        )
    elif status == 400:
        message = (
            "HiBob rejected the request as invalid (400)"
            + (f": {detail.rstrip('.')}." if detail else ".")
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
