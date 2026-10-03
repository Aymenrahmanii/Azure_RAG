"""LLM provider for any OpenAI-compatible chat endpoint (Ollama, OpenAI, Azure OpenAI)."""

import json
from collections.abc import AsyncIterator

import httpx


class OpenAICompatLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120,
        temperature: float | None = None,
    ):
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout
        self._temperature = temperature  # None = model default (GPT-5 family rejects 0)

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                self._url, headers=self._headers, json=self._payload(system, user, False)
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", self._url, headers=self._headers, json=self._payload(system, user, True)
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: ") or line.endswith("[DONE]"):
                        continue
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
