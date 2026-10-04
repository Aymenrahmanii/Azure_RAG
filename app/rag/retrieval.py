"""Configurable retrieval: dense, optional BM25 hybrid (RRF), optional cross-encoder rerank."""

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.providers.base import Chunk, Embedder, RetrievedChunk, VectorStore
from app.security.access import visible

RRF_K = 60
TOKEN_RE = re.compile(r"\w+")


@dataclass
class RetrievalConfig:
    exclude_recitals: bool = False
    hybrid: bool = False
    rerank: bool = False
    candidates: int = 30  # pool size before fusion / reranking
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore, config: RetrievalConfig):
        self.embedder, self.store, self.config = embedder, store, config
        self._chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None
        self._reranker = None
        if config.hybrid:
            self._chunks = store.all_chunks()  # type: ignore[attr-defined]
            if config.exclude_recitals:
                self._chunks = [c for c in self._chunks if c.metadata["section"] != "Recitals"]
            if not self._chunks:
                raise RuntimeError("vector store is empty: run ingestion, check VECTOR_STORE")
            self._bm25 = BM25Okapi([tokenize(c.text) for c in self._chunks])
        if config.rerank:
            from sentence_transformers import CrossEncoder  # heavy (torch): import lazily

            self._reranker = CrossEncoder(config.reranker_model)

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def bm25_scores(self, query: str):
        """BM25 score of every chunk in `chunks` (same order) for `query`."""
        return self._bm25.get_scores(tokenize(query))

    def _dense(self, query: str, n: int) -> list[RetrievedChunk]:
        flt: dict = {}
        if self.config.exclude_recitals:
            flt["section"] = {"$ne": "Recitals"}
        if (allowed := visible()) is not None:  # security trimming, enforced inside the index query
            flt["source"] = {"$in": sorted(allowed)}
        return self.store.search(self.embedder.embed([query])[0], n, flt or None)

    def _sparse(self, query: str, n: int) -> list[RetrievedChunk]:
        scores = self._bm25.get_scores(tokenize(query))
        allowed = visible()
        candidates = [
            i
            for i in range(len(scores))
            if allowed is None or self._chunks[i].metadata["source"] in allowed
        ]
        top = sorted(candidates, key=scores.__getitem__, reverse=True)[:n]
        return [RetrievedChunk(self._chunks[i], float(scores[i])) for i in top]

    @staticmethod
    def _rrf(lists: list[list[RetrievedChunk]]) -> list[RetrievedChunk]:
        fused: dict[str, float] = {}
        by_id: dict[str, Chunk] = {}
        for ranked in lists:
            for rank, rc in enumerate(ranked, 1):
                fused[rc.chunk.id] = fused.get(rc.chunk.id, 0.0) + 1 / (RRF_K + rank)
                by_id[rc.chunk.id] = rc.chunk
        order = sorted(fused, key=fused.__getitem__, reverse=True)
        return [RetrievedChunk(by_id[i], fused[i]) for i in order]

    def retrieve(self, query: str, k: int) -> list[RetrievedChunk]:
        pool = max(self.config.candidates, k)
        results = self._dense(query, pool)
        if self.config.hybrid:
            results = self._rrf([results, self._sparse(query, pool)])
        if self._reranker is not None:
            scores = self._reranker.predict([(query, r.chunk.text) for r in results[:pool]])
            ranked = sorted(zip(results[:pool], scores, strict=True), key=lambda p: -p[1])
            results = [RetrievedChunk(r.chunk, float(s)) for r, s in ranked]
        return results[:k]
