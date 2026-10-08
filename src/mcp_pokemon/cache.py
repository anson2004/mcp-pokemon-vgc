"""In-memory TTL cache with per-key locks, plus a shared HTTP client with retries."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

HOUR = 3600.0
TTL_POKEAPI = 24 * HOUR
TTL_SMOGON_FILE = 6 * HOUR
TTL_LISTING = 1 * HOUR


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_fetch[T](self, key: str, ttl: float, fetch: Callable[[], Awaitable[T]]) -> T:
        hit = self._data.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]  # type: ignore[no-any-return]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._data.get(key)
            now = time.monotonic()
            if hit is not None and hit[0] > now:
                return hit[1]  # type: ignore[no-any-return]
            value = await fetch()
            self._data[key] = (now + ttl, value)
            return value

    def clear(self) -> None:
        self._data.clear()


class Http:
    """Thin wrapper over httpx.AsyncClient with retry on transient failures."""

    def __init__(self, client: httpx.AsyncClient | None = None, retries: int = 3) -> None:
        self._client = client
        self._retries = retries

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                headers={"User-Agent": "mcp-pokemon/0.1 (+https://github.com)"},
                follow_redirects=True,
            )
        return self._client

    async def get(self, url: str) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = await self.client.get(url)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {resp.status_code}", request=resp.request, response=resp
                    )
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
                await asyncio.sleep(0.5 * (2**attempt))
        assert last is not None
        raise last

    async def get_json(self, url: str) -> Any:
        resp = await self.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_text(self, url: str) -> str:
        resp = await self.get(url)
        resp.raise_for_status()
        return resp.text
