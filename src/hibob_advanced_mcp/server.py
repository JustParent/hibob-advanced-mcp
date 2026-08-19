"""MCP server exposing the HiBob Workforce Planning API.

Runs over stdio. Configuration comes from the environment; see config.py.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import (
    ENV_API_HOST,
    ENV_READ_ONLY,
    ENV_SERVICE_USER_ID,
    ENV_SERVICE_USER_TOKEN,
    load_settings,
)
from .workforce_planning import register_workforce_planning_tools

SERVER_NAME = "hibob_advanced_mcp"

# stdout carries the MCP protocol, so all logging goes to stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(SERVER_NAME)


def build_server(read_only: bool | None = None) -> FastMCP:
    """Create the MCP server with the tools this deployment should expose."""
    if read_only is None:
        read_only = load_settings().read_only
    mcp = FastMCP(SERVER_NAME)
    register_workforce_planning_tools(mcp, read_only=read_only)
    return mcp


def _print_diagnostics(mcp: FastMCP) -> None:
    """Print configuration status and the registered tools, then exit.

    Used to verify an install without connecting a client. Credential values
    are never printed, only whether they are set.
    """
    from . import __version__

    settings = load_settings()
    tools = asyncio.run(mcp.list_tools())

    print(f"hibob-advanced-mcp {__version__}")
    print(f"API base URL: {settings.api_base}")
    print(
        "Credentials: "
        + ("configured" if settings.credentials_configured else "NOT SET")
    )
    if not settings.credentials_configured:
        print(f"  Set {ENV_SERVICE_USER_ID} and {ENV_SERVICE_USER_TOKEN}.")
    print(f"Read-only mode: {'on' if settings.read_only else 'off'}")
    print(f"Registered tools ({len(tools)}):")
    for tool in sorted(tools, key=lambda t: t.name):
        print(f"  - {tool.name}")


def main() -> None:
    """Console entry point."""
    parser = argparse.ArgumentParser(
        prog="hibob-advanced-mcp",
        description=(
            "MCP server for the HiBob Workforce Planning API. Reads "
            f"{ENV_SERVICE_USER_ID}, {ENV_SERVICE_USER_TOKEN}, and optionally "
            f"{ENV_API_HOST} and {ENV_READ_ONLY} from the environment."
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Print configuration status and registered tools, then exit.",
    )
    args = parser.parse_args()

    mcp = build_server()

    if args.test:
        _print_diagnostics(mcp)
        return

    mcp.run()


if __name__ == "__main__":
    main()
