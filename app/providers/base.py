"""Provider interfaces. Azure (and later AWS) implementations live beside this file."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str
    metadata: dict = field(default_factory=dict)  # source, article, heading, allowed_groups...


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LLMProvider(Protocol):
    async def generate(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...


class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    def search(
        self, query_embedding: list[float], k: int, filters: dict | None = None
    ) -> list[RetrievedChunk]: ...

    def delete_by_doc(self, doc_id: str) -> None: ...
