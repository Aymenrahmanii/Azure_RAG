"""Semantic answer cache: a paraphrase of a question already answered is served without retrieval
or LLM calls.

Safety rules, because a wrong cache hit is a confidently wrong legal answer:
- The key includes what the caller may see (security trimming), the mode and k: an answer built
  from documents one user can read is never served to a user who cannot.
- A hit needs high cosine similarity AND the same "anchors": the numbers and regulation names in
  the question. Embeddings treat "Article 5" and "Article 6" as near-identical; the anchors do not.
- Only successful answers are stored, entries expire (TTL), and the cache is bounded (oldest out).

It lives in process memory: each replica has its own cache and a restart empties it. A shared
cache (Redis) is the production answer; the interface here would not change.
"""

import hashlib
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

ANCHOR_RE = re.compile(r"\d+|gdpr|nis2?|dora|ai act|annex|article|recital", re.IGNORECASE)


def anchors(question: str) -> frozenset[str]:
    """Tokens that change the meaning of a legal question but barely move its embedding."""
    return frozenset(m.lower() for m in ANCHOR_RE.findall(question))


def scope_key(visible: frozenset[str] | None, mode: str, k: int) -> str:
    seen = "*" if visible is None else ",".join(sorted(visible))
    return hashlib.sha256(f"{mode}|{k}|{seen}".encode()).hexdigest()[:16]


class MemoEmbedder:
    """Remembers single-text embeddings, so a cache miss does not pay for the same query embedding
    twice (once for the cache lookup, once inside retrieval). Batches pass straight through."""

    def __init__(self, inner, size: int = 256):
        self._inner, self._size = inner, size
        self._memo: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if len(texts) != 1:
            return self._inner.embed(texts)
        text = texts[0]
        with self._lock:
            if text in self._memo:
                self._memo.move_to_end(text)
                return [self._memo[text]]
        vec = self._inner.embed(texts)[0]
        with self._lock:
            self._memo[text] = vec
            while len(self._memo) > self._size:
                self._memo.popitem(last=False)
        return [vec]


@dataclass
class CachedAnswer:
    pipeline: str
    kind: str | None
    text: str  # already sanitised
    sources: list = field(default_factory=list)


@dataclass
class _Entry:
    vector: np.ndarray
    anchors: frozenset[str]
    answer: CachedAnswer
    stored_at: float


class SemanticCache:
    def __init__(
        self,
        embed: Callable[[str], list[float]],
        threshold: float = 0.97,
        ttl_seconds: float = 3600,
        max_entries: int = 500,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._embed, self.threshold, self.ttl, self.max_entries = (
            embed,
            threshold,
            ttl_seconds,
            max_entries,
        )
        self._clock = clock
        self._entries: OrderedDict[int, tuple[str, _Entry]] = OrderedDict()
        self._next_id = 0
        self._lock = threading.Lock()

    def _vector(self, question: str) -> np.ndarray:
        v = np.asarray(self._embed(question), dtype=np.float32)
        return v / (np.linalg.norm(v) or 1.0)

    def _expire(self) -> None:
        cutoff = self._clock() - self.ttl
        for key in [k for k, (_, e) in self._entries.items() if e.stored_at < cutoff]:
            del self._entries[key]

    def lookup(self, scope: str, question: str) -> tuple[CachedAnswer | None, float]:
        """(answer or None, best similarity within the scope). Blocking: it embeds the question."""
        vec, want = self._vector(question), anchors(question)
        with self._lock:
            self._expire()
            pool = [(k, e) for k, (s, e) in self._entries.items() if s == scope]
        best, best_key = 0.0, None
        for key, entry in pool:
            sim = float(entry.vector @ vec)
            if sim > best and entry.anchors == want:
                best, best_key = sim, key
        if best_key is not None and best >= self.threshold:
            with self._lock:
                if best_key in self._entries:
                    self._entries.move_to_end(best_key)  # recently used survives eviction longer
            return dict(pool)[best_key].answer, best
        return None, best

    def store(self, scope: str, question: str, answer: CachedAnswer) -> None:
        entry = _Entry(self._vector(question), anchors(question), answer, self._clock())
        with self._lock:
            self._entries[self._next_id] = (scope, entry)
            self._next_id += 1
            self._expire()
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
