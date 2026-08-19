"""MCP server for the HiBob Workforce Planning API."""

from .server import build_server, main

__version__ = "0.1.0"

__all__ = ["build_server", "main", "__version__"]
