"""Read configured HiBob reports and retrieve generated report files."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import HiBobClient, get_client
from .errors import format_exception

REPORTS_PATH = "/company/reports"
DOWNLOAD_PATH = f"{REPORTS_PATH}/download/"
MAX_REPORT_BYTES = 10 * 1024 * 1024
ReportFormat = Literal["json", "csv", "xlsx"]
ReportID = Annotated[
    int, Field(gt=0, strict=True, description="ID from hibob_list_reports.")
]


def _report_name(value: str) -> str:
    """Require one decoded file name, never an arbitrary URL or path."""
    if (
        not value.strip()
        or len(value) > 1024
        or value in {".", ".."}
        or any(c in value for c in ("/", "\\", "%", "?", "#"))
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("report_name must be the file name returned by generation.")
    return value


def _name_from_location(location: str | None) -> str:
    if not location:
        raise ValueError("HiBob did not return the report Location header.")
    parsed = urlsplit(location)
    if (
        parsed.query
        or parsed.fragment
        or not parsed.path.startswith((f"/v1{DOWNLOAD_PATH}", DOWNLOAD_PATH))
    ):
        raise ValueError("HiBob returned an unexpected report Location path.")
    # Never request this URL: Bob's documented Location uses app.hibob.com.
    # Extract only the file name and download through the configured API host.
    return _report_name(unquote(parsed.path.split(DOWNLOAD_PATH, 1)[1]))


def _params(
    format: ReportFormat,
    include_info: bool,
    locale: str | None,
    human_readable: Literal["APPEND", "REPLACE"] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"format": format, "includeInfo": include_info}
    if locale is not None:
        if not locale.strip():
            raise ValueError("locale must not be empty.")
        params["locale"] = locale.strip()
    if human_readable is not None:
        if format != "json":
            raise ValueError("human_readable is supported only for JSON reports.")
        params["humanReadable"] = human_readable
    return params


def _download_result(response: httpx.Response, format: ReportFormat) -> dict[str, Any]:
    if response.status_code == 204:
        return {"status": "pending", "format": format}
    if response.status_code != 200:
        raise ValueError(
            f"Unexpected report response status {response.status_code}; "
            "no report data returned."
        )
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    result: dict[str, Any] = {
        "status": "ready",
        "format": format,
        "content_type": content_type,
        "byte_count": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }
    if format == "json":
        try:
            result["data"] = response.json()
        except ValueError as exc:
            raise ValueError("HiBob did not return a valid JSON report.") from exc
    elif format == "csv":
        if content_type in {"text/html", "application/json"}:
            raise ValueError(
                "HiBob returned an unexpected content type for a CSV report."
            )
        # Keep CSV as text: duplicate headers, leading zeroes, metadata rows and
        # embedded newlines must survive. Do not infer column types or row counts.
        try:
            result["text"] = response.content.decode(response.encoding or "utf-8")
        except (UnicodeError, LookupError) as exc:
            raise ValueError("HiBob did not return a valid text report.") from exc
    else:
        if not response.content.startswith(b"PK\x03\x04"):
            raise ValueError("HiBob did not return an XLSX ZIP file.")
        result["encoding"] = "base64"
        result["data_base64"] = base64.b64encode(response.content).decode("ascii")
    return result


def register_reports_tools(
    mcp: FastMCP,
    *,
    client_factory: Callable[[], HiBobClient] = get_client,
) -> None:
    """Reports do not change employee data; all tools remain in read-only mode."""
    annotations = {
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": True,
    }

    @mcp.tool(
        name="hibob_list_reports",
        annotations=ToolAnnotations(
            title="List available HiBob reports", idempotentHint=True, **annotations
        ),
    )
    async def hibob_list_reports() -> str:
        """List configured reports visible to this service user, including their IDs.

        Returns Bob's JSON metadata unchanged. The list is permission-filtered;
        an absent report is not proof it does not exist. Reports and their filters
        are configured in Bob's UI. This API does not create or edit definitions.
        """
        try:
            response = await client_factory().request_response(
                "GET", REPORTS_PATH, is_read=True, max_bytes=MAX_REPORT_BYTES
            )
            if response.status_code != 200:
                raise ValueError("HiBob did not return report metadata (expected 200).")
            return json.dumps(response.json(), indent=2)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_download_report",
        annotations=ToolAnnotations(
            title="Download a HiBob report by ID", idempotentHint=True, **annotations
        ),
    )
    async def hibob_download_report(
        report_id: ReportID,
        format: ReportFormat = "json",
        include_info: bool = True,
        locale: str | None = None,
        human_readable: Literal["APPEND", "REPLACE"] | None = None,
    ) -> str:
        """Fetch a configured report in JSON (default), CSV or XLSX, without caching.

        JSON is returned as data, CSV as exact text, XLSX as data_base64. Results
        include status, byte_count and sha256. Downloads above 10 MiB are refused
        without truncation. Use scripts for large reports to avoid loading every
        row into an agent conversation. Human-readable options apply only to JSON;
        omit for machine-readable IDs. include_info controls Bob's report metadata.

        For slow reports use hibob_generate_report followed by
        hibob_download_generated_report. Filters and time windows belong to the
        saved Bob report, not this call. A report is not a durable changes cursor
        or a record of which downstream operations have completed.
        """
        try:
            params = _params(format, include_info, locale, human_readable)
            response = await client_factory().request_response(
                "GET",
                f"{REPORTS_PATH}/{report_id}/download",
                params=params,
                is_read=True,
                max_bytes=MAX_REPORT_BYTES,
            )
            result = _download_result(response, format)
            result["report_id"] = report_id
            return json.dumps(result, indent=2)
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_generate_report",
        annotations=ToolAnnotations(
            title="Start generating a HiBob report", idempotentHint=False, **annotations
        ),
    )
    async def hibob_generate_report(
        report_id: ReportID,
        format: Literal["csv", "xlsx"] = "csv",
        include_info: bool = True,
        locale: str | None = None,
    ) -> str:
        """Start async generation of a saved report and return its report_name.

        Uses the documented async formats CSV/XLSX. For JSON use
        hibob_download_report. No automatic retry: even though the endpoint uses
        GET, repeating it can generate another file. A successful response says
        accepted, not ready. Persist report_name and format, then poll
        hibob_download_generated_report; never start generation again to poll.
        Employee data and saved report definitions are not modified.
        """
        try:
            params = _params(format, include_info, locale)
            response = await client_factory().request_response(
                "GET",
                f"{REPORTS_PATH}/{report_id}/download-async",
                params=params,
                max_bytes=MAX_REPORT_BYTES,
            )
            if response.status_code not in {200, 202}:
                raise ValueError(
                    "HiBob did not accept report generation (expected 200/202)."
                )
            return json.dumps(
                {
                    "status": "accepted",
                    "report_id": report_id,
                    "format": format,
                    "report_name": _name_from_location(
                        response.headers.get("Location")
                    ),
                },
                indent=2,
            )
        except Exception as exc:
            return format_exception(exc)

    @mcp.tool(
        name="hibob_download_generated_report",
        annotations=ToolAnnotations(
            title="Download a generated HiBob report",
            idempotentHint=True,
            **annotations,
        ),
    )
    async def hibob_download_generated_report(
        report_name: str,
        format: ReportFormat = "csv",
    ) -> str:
        """Poll once for the file returned by hibob_generate_report.

        Pass report_name and the same format returned by generation. A 204 gives
        status=pending; retry this download later, respecting the 20/min endpoint
        rate limit. A 200 gives status=ready with the complete report. There is no
        sleeping or unbounded polling loop. No URLs, paths or local file writes
        are accepted. The configured HiBob host is used for every request.
        """
        try:
            name = _report_name(report_name)
            response = await client_factory().request_response(
                "GET",
                DOWNLOAD_PATH + quote(name, safe=""),
                is_read=True,
                max_bytes=MAX_REPORT_BYTES,
            )
            result = _download_result(response, format)
            result["report_name"] = name
            return json.dumps(result, indent=2)
        except Exception as exc:
            return format_exception(exc)
