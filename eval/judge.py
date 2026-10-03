"""LLM-as-judge: one call per question returns faithfulness, relevance, correctness, declined."""

import json
import re

from app.providers.base import LLMProvider, RetrievedChunk

JUDGE_SYSTEM = """You are a strict evaluator of a retrieval-augmented question answering system.
You receive a question, the numbered context passages given to the system, the system's answer,
and a reference answer. Reply with ONE JSON object and nothing else:
{
 "faithfulness": number 0-1, share of the answer's factual claims that are supported by the
   context passages (1 = every claim supported, 0 = none; a refusal with no claims = 1),
 "relevance": number 0-1, how directly the answer addresses the question (a correct refusal of an
   unanswerable question = 1),
 "correctness": number 0-1, agreement with the reference answer (1 = same key facts,
   0.5 = partially correct or incomplete, 0 = wrong or contradicts it),
 "declined": true if the answer declines to answer / says it does not know / refuses,
   otherwise false,
 "reason": one short sentence
}"""


def build_judge_prompt(question: str, sources: list[RetrievedChunk], answer: str, ref: str) -> str:
    ctx = "\n\n".join(f"[{i}] {s.chunk.text}" for i, s in enumerate(sources, 1))
    return (
        f"Question:\n{question}\n\nContext passages:\n{ctx}\n\n"
        f"System answer:\n{answer}\n\nReference answer:\n{ref}"
    )


def parse_judge(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON in judge output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    for key in ("faithfulness", "relevance", "correctness"):
        data[key] = max(0.0, min(1.0, float(data[key])))
    data["declined"] = bool(data["declined"])
    return data


async def judge(
    llm: LLMProvider, question: str, sources: list[RetrievedChunk], answer: str, ref: str
) -> dict:
    raw = await llm.generate(JUDGE_SYSTEM, build_judge_prompt(question, sources, answer, ref))
    return parse_judge(raw)
