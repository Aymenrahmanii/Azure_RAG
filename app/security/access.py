"""Document-level access control ("security trimming").

A policy says which documents are restricted and which groups may read them. At request time the
API computes the set of sources the caller may see and stores it in a ContextVar; every retrieval
path (dense search, BM25, graph expansion, agent tools, community summaries) reads it from there.
A ContextVar (not a parameter) so no layer can forget to pass it, and it follows asyncio tasks and
`asyncio.to_thread` automatically.

`None` means "no restriction" (local development, startup warm-up). The API always sets it for
authenticated requests, and refuses to start without authentication outside `local`.
"""

import json
from collections.abc import Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

_visible: ContextVar[frozenset[str] | None] = ContextVar("visible_sources", default=None)


def visible() -> frozenset[str] | None:
    return _visible.get()


def set_visible(sources: Iterable[str] | None) -> Token:
    return _visible.set(None if sources is None else frozenset(sources))


def reset_visible(token: Token) -> None:
    _visible.reset(token)


def is_visible(source: str) -> bool:
    allowed = _visible.get()
    return allowed is None or source in allowed


def section_visible(section_key: str) -> bool:
    """Section keys look like 'gdpr:Article 33'; the part before the first colon is the source."""
    return is_visible(section_key.split(":", 1)[0])


@dataclass(frozen=True)
class AccessPolicy:
    # source -> groups of which the caller needs at least one. Sources not listed are open.
    restricted: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> "AccessPolicy":
        raw = json.loads(text) if text.strip() else {}
        return cls({source: frozenset(groups) for source, groups in raw.items()})

    def visible_sources(self, groups: Iterable[str], all_sources: Iterable[str]) -> frozenset[str]:
        mine = set(groups)
        return frozenset(
            s for s in all_sources if s not in self.restricted or self.restricted[s] & mine
        )
