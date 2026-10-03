"""Evaluation harness.

python -m eval.run --label baseline                # retrieval + generation + judge
python -m eval.run --label retrieval-only --no-generate   # free: retrieval metrics only
"""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.providers.local import ChromaStore, SentenceTransformerEmbedder
from app.providers.openai_compat import ContentFiltered, OpenAICompatLLM
from app.rag import chunking
from app.rag.pipeline import SYSTEM_PROMPT, build_prompt
from eval.judge import judge
from eval.metrics import (
    citation_precision,
    distinct_sections,
    mean,
    recall_at_k,
    reciprocal_rank,
    recital_share,
)

DATASET = Path("eval/dataset.jsonl")
RESULTS = Path("eval/results")
K_VALUES = (1, 3, 5, 10)
REFUSAL_PHRASE = "I don't know based on the provided documents"


def retrieval_metrics(row: dict, retrieved: list, k: int) -> dict:
    out = {
        "recital_share": recital_share(retrieved, k),
        "distinct_sections": distinct_sections(retrieved, k),
        "top1_score": retrieved[0].score,
    }
    if row["expected_sections"]:
        exp = row["expected_sections"]
        out.update({f"recall@{n}": recall_at_k(retrieved, exp, n) for n in K_VALUES})
        out["mrr"] = reciprocal_rank(retrieved, exp)
    return out


async def evaluate_row(row, retrieved, k, llm, sem):
    sources = retrieved[:k]
    async with sem:
        t0 = time.perf_counter()
        try:
            answer = await llm.generate(SYSTEM_PROMPT, build_prompt(row["question"], sources))
        except ContentFiltered:
            # Blocked by the provider's prompt shield before reaching the model: a refusal.
            ok = row["expected_behavior"] == "abstain"
            return {
                "answer": "[blocked by provider content filter]",
                "latency_s": round(time.perf_counter() - t0, 2),
                "blocked": True,
                "faithfulness": 1.0,
                "relevance": float(ok),
                "correctness": float(ok),
                "declined": True,
                "reason": "blocked by content filter",
            }
        latency = time.perf_counter() - t0
        try:
            verdict = await judge(llm, row["question"], sources, answer, row["reference_answer"])
        except ContentFiltered:
            # The judge prompt quotes the injection text and trips the same filter.
            verdict = {
                "faithfulness": None,
                "relevance": None,
                "correctness": None,
                "declined": REFUSAL_PHRASE in answer,
                "reason": "judge blocked by content filter; declined from phrase match",
                "judge_blocked": True,
            }
    res = {"answer": answer, "latency_s": round(latency, 2), "blocked": False, **verdict}
    if row["expected_sections"]:
        res["citation_precision"] = citation_precision(answer, sources, row["expected_sections"])
    return res


def summarize(rows: list[dict]) -> dict:
    """Aggregate per-question results into overall and per-type numbers."""

    def agg(subset: list[dict]) -> dict:
        def m(key, src="retrieval"):
            return mean([r[src][key] for r in subset if r.get(src) and r[src].get(key) is not None])

        out = {"n": len(subset)}
        for key in [f"recall@{n}" for n in K_VALUES] + ["mrr", "recital_share"]:
            out[key] = m(key)
        answerable = [
            r for r in subset if r["expected_behavior"] == "answer" and r.get("generation")
        ]
        abstain = [r for r in subset if r["expected_behavior"] == "abstain" and r.get("generation")]
        gen = [r for r in subset if r.get("generation")]
        for key in ("faithfulness", "relevance", "correctness"):
            out[key] = mean([r["generation"][key] for r in gen if r["generation"][key] is not None])
        out["citation_precision"] = mean(
            [
                r["generation"]["citation_precision"]
                for r in answerable
                if r["generation"].get("citation_precision") is not None
            ]
        )
        out["false_refusal_rate"] = mean([float(r["generation"]["declined"]) for r in answerable])
        out["correct_abstention_rate"] = mean([float(r["generation"]["declined"]) for r in abstain])
        out["latency_p50_s"] = percentile([r["generation"]["latency_s"] for r in gen], 50)
        out["latency_p95_s"] = percentile([r["generation"]["latency_s"] for r in gen], 95)
        return out

    types = sorted({r["type"] for r in rows})
    return {
        "overall": agg(rows),
        "by_type": {t: agg([r for r in rows if r["type"] == t]) for t in types},
    }


def percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


def fmt(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def print_table(summary: dict) -> None:
    cols = [
        "recall@5",
        "mrr",
        "recital_share",
        "faithfulness",
        "correctness",
        "citation_precision",
        "false_refusal_rate",
        "correct_abstention_rate",
    ]
    print("\n| group | n | " + " | ".join(cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    groups = {"OVERALL": summary["overall"], **summary["by_type"]}
    for name, s in groups.items():
        print(f"| {name} | {s['n']} | " + " | ".join(fmt(s[c]) for c in cols) + " |")
    o = summary["overall"]
    print(f"\nlatency p50 {fmt(o['latency_p50_s'])}s, p95 {fmt(o['latency_p95_s'])}s")


async def main_async(args) -> None:
    dataset = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    if args.limit:
        dataset = dataset[: args.limit]
    embedder = SentenceTransformerEmbedder(settings.embedding_model)
    store = ChromaStore(settings.chroma_path)
    llm = None
    if not args.no_generate:
        llm = OpenAICompatLLM(settings.llm_base_url, settings.llm_model, settings.llm_api_key)

    questions = [r["question"] for r in dataset]
    qvecs = embedder.embed(questions)
    retrieved_all = [store.search(v, max(args.k, max(K_VALUES))) for v in qvecs]

    sem = asyncio.Semaphore(args.concurrency)
    gen_results = [None] * len(dataset)
    if llm:
        tasks = [
            evaluate_row(row, ret, args.k, llm, sem)
            for row, ret in zip(dataset, retrieved_all, strict=True)
        ]
        gen_results = await asyncio.gather(*tasks)

    rows = []
    for row, ret, gen in zip(dataset, retrieved_all, gen_results, strict=True):
        rows.append(
            {
                "id": row["id"],
                "type": row["type"],
                "question": row["question"],
                "expected_behavior": row["expected_behavior"],
                "expected_sections": row["expected_sections"],
                "retrieved": [
                    f"{r.chunk.metadata['source']}:{r.chunk.metadata['section']} ({r.score:.2f})"
                    for r in ret[: args.k]
                ],
                "retrieval": retrieval_metrics(row, ret, args.k),
                "generation": gen,
            }
        )
    summary = summarize(rows)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{stamp}_{args.label}.json"
    config = {
        "label": args.label,
        "k": args.k,
        "embedding_model": settings.embedding_model,
        "llm_model": None if args.no_generate else settings.llm_model,
        "chunk_max_chars": chunking.MAX_CHARS,
        "n_chunks": store.count(),
    }
    out.write_text(
        json.dumps(
            {"config": config, "summary": summary, "rows": rows}, indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print_table(summary)
    print(f"\nsaved {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--no-generate", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=4)
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
