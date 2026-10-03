"""Embeddings from an Azure OpenAI deployment (OpenAI-compatible /embeddings), Entra auth."""

import time
from collections.abc import Callable

import httpx

BATCH = 64
RETRYABLE = {429, 500, 502, 503, 504}


class AzureOpenAIEmbedder:
    def __init__(
        self,
        base_url: str,
        deployment: str,
        token_provider: Callable[[], str],
        timeout: float = 60,
        max_retries: int = 5,
    ):
        self._url = base_url.rstrip("/") + "/embeddings"
        self._deployment = deployment
        self._token_provider = token_provider
        self._timeout = timeout
        self._max_retries = max_retries

    def _post(self, client: httpx.Client, texts: list[str]) -> list[list[float]]:
        for attempt in range(self._max_retries + 1):
            resp = client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token_provider()}"},
                json={"model": self._deployment, "input": texts},
            )
            if resp.status_code not in RETRYABLE or attempt == self._max_retries:
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in data]
            time.sleep(min(float(resp.headers.get("retry-after", 2.0**attempt)), 60))
        raise RuntimeError("unreachable")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            for i in range(0, len(texts), BATCH):
                out.extend(self._post(client, texts[i : i + BATCH]))
        return out
