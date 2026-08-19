"""Environment-driven configuration for the HiBob Workforce Planning MCP server.

Settings are read at call time rather than import time so that the process can
start (and list its tools) before credentials are present, and so tests can
change the environment without reloading modules.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

ENV_SERVICE_USER_ID = "HIBOB_SERVICE_USER_ID"
ENV_SERVICE_USER_TOKEN = "HIBOB_SERVICE_USER_TOKEN"
ENV_API_HOST = "HIBOB_API_HOST"
ENV_READ_ONLY = "HIBOB_READ_ONLY"

DEFAULT_API_HOST = "api.hibob.com"
API_VERSION_PATH = "/v1"

_TRUTHY = {"1", "true", "yes", "on"}

# Hosts already warned about, so repeated settings reads stay quiet.
_warned_hosts: set[str] = set()


def parse_hibob_api_host(raw: str) -> str:
    """Extract a HiBob API hostname from a host or pasted URL.

    Accepts a bare hostname (``api.sandbox.hibob.com``), a full URL
    (``https://api.sandbox.hibob.com/v1``), or a protocol-relative URL
    (``//api.sandbox.hibob.com``). Path segments and ports are dropped so
    callers can build API URLs themselves.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" in s or s.startswith("//"):
        if s.startswith("//"):
            s = f"https:{s}"
        return urlparse(s).hostname or ""
    return s.split("/")[0].split(":")[0]


def hibob_api_base(raw_host: str) -> str:
    """Build the versioned API base URL, defaulting to production."""
    host = parse_hibob_api_host(raw_host) or DEFAULT_API_HOST
    return f"https://{host}{API_VERSION_PATH}"


def read_only_enabled() -> bool:
    """True when write tools should not be registered."""
    return os.environ.get(ENV_READ_ONLY, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Settings:
    """Resolved server configuration."""

    service_user_id: str
    service_user_token: str
    api_base: str
    read_only: bool

    @property
    def credentials_configured(self) -> bool:
        return bool(self.service_user_id and self.service_user_token)


def load_settings() -> Settings:
    """Read configuration from the environment."""
    raw_host = os.environ.get(ENV_API_HOST, "")
    api_base = hibob_api_base(raw_host)
    host = parse_hibob_api_host(raw_host)
    if host and not host.endswith(".hibob.com") and host not in _warned_hosts:
        _warned_hosts.add(host)
        print(
            f"warning: {ENV_API_HOST} resolves to {host!r}, which is not a "
            "*.hibob.com host. Service user credentials will be sent there.",
            file=sys.stderr,
        )
    return Settings(
        service_user_id=os.environ.get(ENV_SERVICE_USER_ID, "").strip(),
        service_user_token=os.environ.get(ENV_SERVICE_USER_TOKEN, "").strip(),
        api_base=api_base,
        read_only=read_only_enabled(),
    )
