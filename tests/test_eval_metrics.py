import pytest

from app.providers.base import Chunk, RetrievedChunk
from eval.judge import parse_judge
from eval.metrics import (
    citation_precision,
    parse_citations,
    recall_at_k,
    reciprocal_rank,
    recital_share,
)


def rc(source: str, section: str) -> RetrievedChunk:
    return RetrievedChunk(Chunk("id", source, "t", {"source": source, "section": section}), 0.5)


RET = [
    rc("gdpr", "Recitals"),
    rc("gdpr", "Article 33"),
    rc("nis2", "Article 23"),
    rc("gdpr", "Article 5"),
]
EXP = ["gdpr:Article 33", "nis2:Article 23"]


def test_recall_and_mrr():
    assert recall_at_k(RET, EXP, 1) == 0
    assert recall_at_k(RET, EXP, 2) == 0.5
    assert recall_at_k(RET, EXP, 3) == 1
    assert reciprocal_rank(RET, EXP) == 0.5
    assert reciprocal_rank(RET, ["gdpr:Article 99"]) == 0


def test_recital_share():
    assert recital_share(RET, 4) == 0.25


def test_citation_parsing_and_precision():
    assert parse_citations("Yes [1][3] and also [2, 4].") == {1, 2, 3, 4}
    assert citation_precision("Answer [2][3].", RET, EXP) == 1.0
    assert citation_precision("Answer [1][2].", RET, EXP) == 0.5
    assert citation_precision("No citations here.", RET, EXP) is None
    assert citation_precision("Out of range [9].", RET, EXP) is None


def test_parse_judge_clamps_and_tolerates_fences():
    raw = '```json\n{"faithfulness": 1.4, "relevance": 0.5, "correctness": 1, "declined": 0}\n```'
    out = parse_judge(raw)
    assert out["faithfulness"] == 1.0 and out["declined"] is False
    with pytest.raises(ValueError):
        parse_judge("sorry")
