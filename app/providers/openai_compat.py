"""LLM provider for any OpenAI-compatible chat endpoint (Ollama, OpenAI, Azure OpenAI)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from app.observability import stage

RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class Usage:
    """Token and call counts. Set `usage_var` in a task to meter everything that task spends."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, raw: dict | None) -> None:
        self.calls += 1
        if raw:
            self.prompt_tokens += raw.get("prompt_tokens", 0)
            self.completion_tokens += raw.get("completion_tokens", 0)


# A ContextVar (not an attribute on the LLM) so concurrent requests are metered separately.
usage_var: ContextVar[Usage | None] = ContextVar("usage", default=None)


class ContentFiltered(Exception):
    """The provider's content filter / prompt shield rejected the request."""


def parse_stream_line(line: str) -> str | None:
    """Text delta from one SSE line, or None. Azure sends chunks with `choices: []` (content-filter
    annotations) and role-only deltas, so a missing choice or content is normal, not an error."""
    if not line.startswith("data: ") or line.endswith("[DONE]"):
        return None
    choices = json.loads(line[6:]).get("choices") or []
    return (choices[0].get("delta") or {}).get("content") if choices else None


def parse_stream_usage(line: str) -> dict | None:
    """The `usage` object of the final stream chunk (requested with include_usage), or None."""
    if not line.startswith("data: ") or line.endswith("[DONE]"):
        return None
    return json.loads(line[6:]).get("usage")


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
        return self._body(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], stream
        )

    def _body(self, messages: list[dict], stream: bool, tools: list[dict] | None = None) -> dict:
        payload = {"model": self._model, "stream": stream, "messages": messages}
        if tools:
            payload["tools"] = tools
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        return payload

    async def _post(self, payload: dict) -> dict:
        """POST with retries on 429 / 5xx / timeouts: exponential backoff, honours Retry-After."""
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
                        data = resp.json()
                        if meter := usage_var.get():
                            meter.add(data.get("usage"))
                        return data
                    delay = float(resp.headers.get("retry-after", 2.0**attempt))
                await asyncio.sleep(min(delay, 60))
        raise RuntimeError("unreachable")

    async def generate(self, system: str, user: str) -> str:
        with stage("llm.generate"):
            data = await self._post(self._payload(system, user, False))
        return data["choices"][0]["message"]["content"]

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """One chat turn that may call tools; returns the assistant message
        ({"content": ..., "tool_calls": [...]})."""
        with stage("llm.chat"):
            data = await self._post(self._body(messages, False, tools))
        return data["choices"][0]["message"]

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        payload = self._payload(system, user, True)
        payload["stream_options"] = {"include_usage": True}  # final chunk carries the token counts
        with stage("llm.stream"):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", self._url, headers=self._headers(), json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if delta := parse_stream_line(line):
                            yield delta
                        elif usage := parse_stream_usage(line):
                            if meter := usage_var.get():
                                meter.add(usage)
