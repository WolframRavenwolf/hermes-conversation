"""HTTP client for the Hermes Agent API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import aiohttp

from .const import (
    API_CHAT_COMPLETIONS,
    API_HEALTH,
    API_MODELS,
    DEFAULT_STREAM_TIMEOUT,
    DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class HermesApiError(Exception):
    """Base exception for Hermes API errors."""


class HermesConnectionError(HermesApiError):
    """Cannot reach the Hermes Agent API."""


class HermesAuthError(HermesApiError):
    """Authentication failed."""


class HermesApiClient:
    """Client for the Hermes Agent OpenAI-compatible API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        api_key: str | None = None,
        use_ssl: bool = False,
    ) -> None:
        self._session = session
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"
        self._api_key = api_key

    @property
    def base_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def async_check_connection(self) -> bool:
        """Check if the Hermes Agent API is reachable and auth is valid."""
        try:
            async with self._session.get(
                f"{self._base_url}{API_HEALTH}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise HermesAuthError("Invalid API key")
                if resp.status == 403:
                    raise HermesAuthError("Access denied")
                return resp.status < 400
        except HermesAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HermesConnectionError(
                f"Cannot connect to Hermes Agent at {self._base_url}: {err}"
            ) from err

    async def async_get_models(self) -> list[dict[str, Any]]:
        """Fetch available models from /v1/models."""
        try:
            async with self._session.get(
                f"{self._base_url}{API_MODELS}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("data", [])
        except Exception:
            return []

    async def async_send_message(
        self,
        messages: list[dict[str, str]],
        model: str = "hermes-agent",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Send a non-streaming chat completion request. Returns the response content."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            async with self._session.post(
                f"{self._base_url}{API_CHAT_COMPLETIONS}",
                headers=self._headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise HermesAuthError("Invalid API key")
                if resp.status >= 400:
                    body = await resp.text()
                    raise HermesApiError(
                        f"API error {resp.status}: {body[:500]}"
                    )
                data = await resp.json()
                return self._extract_content(data)
        except HermesApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HermesConnectionError(
                f"Connection error: {err}"
            ) from err

    async def async_stream_message(
        self,
        messages: list[dict[str, str]],
        model: str = "hermes-agent",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Send a streaming chat completion request. Yields content deltas."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        try:
            async with self._session.post(
                f"{self._base_url}{API_CHAT_COMPLETIONS}",
                headers=self._headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=DEFAULT_STREAM_TIMEOUT,
                    sock_read=DEFAULT_TIMEOUT,
                ),
            ) as resp:
                if resp.status == 401:
                    raise HermesAuthError("Invalid API key")
                if resp.status >= 400:
                    body = await resp.text()
                    raise HermesApiError(
                        f"API error {resp.status}: {body[:500]}"
                    )

                # Parse SSE stream
                buffer = ""
                async for chunk in resp.content.iter_any():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()

                        if not line:
                            continue
                        if line == "data: [DONE]":
                            return
                        if not line.startswith("data: "):
                            continue

                        try:
                            data = json.loads(line[6:])
                            delta = (
                                data.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                            )
                            if delta:
                                yield delta
                        except (json.JSONDecodeError, IndexError):
                            continue

        except HermesApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HermesConnectionError(
                f"Stream connection error: {err}"
            ) from err

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return data.get("error", {}).get("message", "(No response)")
