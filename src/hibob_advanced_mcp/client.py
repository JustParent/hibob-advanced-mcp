"""HTTP client for the HiBob API.

Authenticates with service user credentials over HTTP Basic auth. Read calls
are retried on transient failures; writes never are, because position creation
is not idempotent and HiBob only allows ten write calls per minute.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .config import (
    ENV_SERVICE_USER_ID,
    ENV_SERVICE_USER_TOKEN,
    Settings,
    load_settings,
)
from .errors import HiBobConfigError, raise_for_hibob_error

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_READ_RETRIES = 2
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_DELAY_SECONDS = 10.0

SleepFn = Callable[[float], Awaitable[None]]


class HiBobClient:
    """Thin async wrapper over the HiBob REST API."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sleep: SleepFn | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._client: httpx.AsyncClient | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    def _require_credentials(self) -> None:
        if not self._settings.credentials_configured:
            raise HiBobConfigError(
                "HiBob credentials are not configured. Set "
                f"{ENV_SERVICE_USER_ID} and {ENV_SERVICE_USER_TOKEN} to the ID and "
                "token of a HiBob API service user."
            )

    def _get_client(self) -> httpx.AsyncClient:
        """Build the shared client lazily.

        No default ``Content-Type`` is set: httpx picks the right value per
        request from the body argument, and a fixed default would break any
        future multipart upload.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._settings.api_base,
                auth=httpx.BasicAuth(
                    self._settings.service_user_id,
                    self._settings.service_user_token,
                ),
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
            except ValueError:
                pass
        return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        is_read: bool = False,
    ) -> Any:
        """Send a request and return the decoded JSON body.

        Returns ``None`` for empty bodies (HiBob answers some writes with 204).
        """
        self._require_credentials()
        client = self._get_client()

        attempt = 0
        while True:
            response = await client.request(method, path, json=json)
            if (
                is_read
                and response.status_code in RETRYABLE_STATUSES
                and attempt < MAX_READ_RETRIES
            ):
                await self._sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue
            break

        raise_for_hibob_error(response)

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    async def get(self, path: str) -> Any:
        return await self.request("GET", path, is_read=True)

    async def search(self, path: str, body: dict[str, Any]) -> Any:
        """POST a search query. Safe to retry, unlike other POSTs."""
        return await self.request("POST", path, json=body, is_read=True)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self.request("POST", path, json=body)

    async def patch(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self.request("PATCH", path, json=body)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)


_client: HiBobClient | None = None


def get_client() -> HiBobClient:
    """Return the process-wide client, creating it on first use."""
    global _client
    if _client is None:
        _client = HiBobClient()
    return _client


def reset_client() -> None:
    """Drop the cached client so the next call re-reads the environment."""
    global _client
    _client = None
