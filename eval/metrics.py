"""Pure metric functions (no network), unit-tested in tests/test_eval_metrics.py."""

import re

from app.providers.base import RetrievedChunk

CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def section_id(rc: RetrievedChunk) -> str:
    return f"{rc.chunk.metadata['source']}:{rc.chunk.metadata['section']}"


def recall_at_k(retrieved: list[RetrievedChunk], expected: list[str], k: int) -> float:
    """Fraction of expected sections present in the top-k chunks."""
    found = {section_id(r) for r in retrieved[:k]}
    return len(found & set(expected)) / len(expected)


def reciprocal_rank(retrieved: list[RetrievedChunk], expected: list[str]) -> float:
    for rank, r in enumerate(retrieved, 1):
        if section_id(r) in expected:
            return 1 / rank
    return 0.0


def recital_share(retrieved: list[RetrievedChunk], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(r.chunk.metadata["section"] == "Recitals" for r in top) / len(top)


def distinct_sections(retrieved: list[RetrievedChunk], k: int) -> int:
    return len({section_id(r) for r in retrieved[:k]})


def parse_citations(text: str) -> set[int]:
    nums: set[int] = set()
    for group in CITATION_RE.findall(text):
        nums.update(int(n) for n in re.split(r"\s*,\s*", group))
    return nums


def citation_precision(
    answer: str, retrieved: list[RetrievedChunk], expected: list[str]
) -> float | None:
    """Share of cited passages whose section is an expected one. None if nothing is cited."""
    cited = [n for n in parse_citations(answer) if 1 <= n <= len(retrieved)]
    if not cited:
        return None
    return sum(section_id(retrieved[n - 1]) in expected for n in cited) / len(cited)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
