import json
from pathlib import Path

import pytest

from app.rag.chunking import split_sections

DATASET = Path("eval/dataset.jsonl")
RAW = Path("data/raw")
TYPES = {
    "direct",
    "multi_article",
    "cross_regulation",
    "out_of_scope",
    "adversarial",
    "ambiguous",
    "partial",
    "global",
}


def load() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]


def test_schema_and_unique_ids():
    rows = load()
    assert len(rows) >= 50
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["type"] in TYPES
        assert r["expected_behavior"] in {"answer", "abstain"}
        assert r["question"] and r["reference_answer"]
        if r["expected_behavior"] == "answer":
            assert r["expected_sections"], f"{r['id']} answerable but no expected sections"


@pytest.mark.skipif(not RAW.exists(), reason="corpus not downloaded")
def test_expected_sections_exist_in_corpus():
    known = {
        f"{p.stem}:{s.label}"
        for p in RAW.glob("*.txt")
        for s in split_sections(p.read_text(encoding="utf-8"))
    }
    missing = [(r["id"], ref) for r in load() for ref in r["expected_sections"] if ref not in known]
    assert not missing, missing
