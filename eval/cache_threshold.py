"""Choose the semantic-cache similarity threshold from data, not from a guess.

    python -m eval.cache_threshold

Three measurements with the configured embedder (the one the API uses):
  paraphrases      pairs that mean the same thing: these SHOULD hit
  hard negatives   pairs that differ in meaning but share all anchors (numbers, regulation names),
                   so the anchor guard cannot catch them: these must NOT hit
  distinct         every pair of different eval-set questions that share anchors: the highest
                   similarity here is the floor below which the threshold causes wrong hits
"""

import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.providers.factory import make_embedder
from app.rag.cache import anchors

PARAPHRASES = [
    (
        "Within how many hours must a controller notify the supervisory authority of a personal data breach?",
        "How many hours does a controller have to report a personal data breach to the supervisory authority?",
    ),
    (
        "At what age can a child consent to information society services without parental authorisation under the GDPR?",
        "Under the GDPR, from what age may a child give consent to online services without a parent's approval?",
    ),
    (
        "What is the maximum administrative fine for the most serious GDPR infringements?",
        "What is the highest administrative fine the GDPR allows for the most serious infringements?",
    ),
    (
        "When must a controller or processor designate a data protection officer?",
        "In which cases do a controller or processor have to appoint a data protection officer?",
    ),
    (
        "When is a data protection impact assessment required?",
        "In what situations do you need to carry out a data protection impact assessment?",
    ),
    (
        "Which AI practices are prohibited under the EU AI Act?",
        "What AI practices does the EU AI Act prohibit?",
    ),
    (
        "What are the incident reporting deadlines for significant incidents under NIS2?",
        "What deadlines does NIS2 set for reporting significant incidents?",
    ),
    (
        "How often must financial entities identified under DORA carry out threat-led penetration testing?",
        "How frequently does DORA require threat-led penetration testing by the financial entities concerned?",
    ),
    (
        "What are the legal bases for lawful processing under the GDPR?",
        "Which legal bases make processing lawful under the GDPR?",
    ),
    (
        "How do the breach notification deadlines of the GDPR and NIS2 compare?",
        "Compare the GDPR and NIS2 deadlines for notifying a breach.",
    ),
]

HARD_NEGATIVES = [
    (
        "Within how many hours must a controller notify the supervisory authority of a personal data breach?",
        "Within how many hours must a processor notify the controller of a personal data breach?",
    ),
    (
        "When is a data protection impact assessment required?",
        "When is a data protection impact assessment not required?",
    ),
    (
        "What is the maximum administrative fine for the most serious GDPR infringements?",
        "What is the minimum administrative fine for the most serious GDPR infringements?",
    ),
    (
        "How often must financial entities identified under DORA carry out threat-led penetration testing?",
        "How often must financial entities under DORA test their business continuity plans?",
    ),
    (
        "What are the incident reporting deadlines for significant incidents under NIS2?",
        "What are the incident reporting obligations for significant incidents under NIS2?",
    ),
    (
        "Which AI practices are prohibited under the EU AI Act?",
        "Which AI practices are permitted under the EU AI Act?",
    ),
]

THRESHOLDS = [0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98]


def normalised(vectors: list[list[float]]) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def similarity(embed, pairs) -> list[float]:
    flat = [q for pair in pairs for q in pair]
    m = normalised(embed(flat))
    return [float(m[i] @ m[i + 1]) for i in range(0, len(flat), 2)]


def main() -> None:
    embedder = make_embedder(settings.embedding_model, settings)
    embed = embedder.embed
    para, neg = similarity(embed, PARAPHRASES), similarity(embed, HARD_NEGATIVES)

    questions = [
        json.loads(line)["question"]
        for line in Path("eval/dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    m = normalised(embed(questions))
    distinct = sorted(
        (
            (float(m[i] @ m[j]), questions[i], questions[j])
            for i, j in combinations(range(len(questions)), 2)
            if anchors(questions[i]) == anchors(questions[j])
        ),
        reverse=True,
    )

    print(f"embedder: {settings.embedding_model}")
    print(f"paraphrases    min {min(para):.3f}  median {np.median(para):.3f}  max {max(para):.3f}")
    print(f"hard negatives min {min(neg):.3f}  median {np.median(neg):.3f}  max {max(neg):.3f}")
    print(f"distinct eval questions sharing anchors: {len(distinct)} pairs, top 3:")
    for sim, a, b in distinct[:3]:
        print(f"  {sim:.3f}  {a[:60]!r} ~ {b[:60]!r}")
    print("\nthreshold  paraphrase hit rate  hard-negative false hits  distinct false hits")
    for t in THRESHOLDS:
        print(
            f"{t:>9.2f}  {sum(s >= t for s in para):>2}/{len(para):<17} "
            f"{sum(s >= t for s in neg):>2}/{len(neg):<22} {sum(d[0] >= t for d in distinct):>3}"
        )
    json.dump(
        {"paraphrases": para, "hard_negatives": neg, "distinct_top": [d[0] for d in distinct[:20]]},
        open("eval/results/cache-threshold.json", "w"),
        indent=1,
    )


if __name__ == "__main__":
    sys.exit(main())
