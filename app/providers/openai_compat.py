"""LLM provider for any OpenAI-compatible chat endpoint (Ollama, OpenAI, Azure OpenAI)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx

RETRYABLE = {429, 500, 502, 503, 504}


class ContentFiltered(Exception):
    """The provider's content filter / prompt shield rejected the request."""


def parse_stream_line(line: str) -> str | None:
    """Text delta from one SSE line, or None. Azure sends chunks with `choices: []` (content-filter
    annotations) and role-only deltas, so a missing choice or content is normal, not an error."""
    if not line.startswith("data: ") or line.endswith("[DONE]"):
        return None
    choices = json.loads(line[6:]).get("choices") or []
    return (choices[0].get("delta") or {}).get("content") if choices else None


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120,
        temperature: float | None = None,
        max_retries: int = 4,
        token_provider: Callable[[], str] | None = None,
    ):
        self._token_provider = token_provider  # Entra ID: fresh bearer token per request
        self._max_retries = max_retries
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._temperature = temperature  # None = model default (GPT-5 family rejects 0)

    def _headers(self) -> dict:
        token = self._token_provider() if self._token_provider else self._api_key
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _payload(self, system: str, user: str, stream: bool) -> dict:
        payload = {
            "model": self._model,
            "stream": stream,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    async def generate(self, system: str, user: str) -> str:
        """POST with retries on 429 / 5xx / timeouts: exponential backoff, honours Retry-After."""
        payload = self._payload(system, user, False)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    resp = await client.post(self._url, headers=self._headers(), json=payload)
                except httpx.TransportError:
                    if attempt == self._max_retries:
                        raise
                    delay = 2.0**attempt
                else:
                    if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                        if resp.status_code == 400 and "content management policy" in resp.text:
                            raise ContentFiltered(resp.text[:300])
                        resp.raise_for_status()
                        return resp.json()["choices"][0]["message"]["content"]
                    delay = float(resp.headers.get("retry-after", 2.0**attempt))
                await asyncio.sleep(min(delay, 60))
        raise RuntimeError("unreachable")

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._url, headers=self._headers(), json=self._payload(system, user, True)
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if delta := parse_stream_line(line):
                        yield delta
