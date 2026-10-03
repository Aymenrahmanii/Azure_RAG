# Experiment log

Every change to the pipeline gets a row here, with the eval run that proves it.
Run: `python -m eval.run --label <name>` (add `--no-generate` for free retrieval-only runs).
Judge and generator are the same model (gpt-5.4-mini), so judge scores are optimistic; trends matter more than absolutes.

| # | Label | Change | recall@5 | MRR | recital share | faithfulness | correctness | citation prec. | false refusals | correct abstention | p50 / p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | MiniLM embeddings, article-aware chunks (max 1500 chars), top-5 dense, strict "I don't know" prompt | 0.73 | 0.72 | 0.35 | 0.98 | 0.75 | 0.71 | 0.29 | 1.00 | 2.9 s / 11.0 s |

## Baseline findings (run 20261003-183218)

Weakest groups: cross-regulation (recall@5 0.50, correctness 0.50) and multi-article (recall@5 0.62, citation precision 0.41).

Two distinct failure modes, so they need two different fixes:

1. **Retrieval misses.** Short, dense legal sections lose to long or generic text. Examples:
   AI Act Article 5 (prohibited practices) was outranked by Articles 108-110 (amendments); Annex III,
   NIS2 Article 20 and DORA Article 5 were not retrieved at all. 35% of top-5 slots are recitals,
   which repeat the articles' wording and crowd them out. -> hybrid (BM25 + dense), reranker,
   recital down-weighting or separate index, larger candidate pool, query rewriting, better embeddings.
2. **Over-refusal with the right context.** In ~7 questions the context contained the answer
   (or part of it) but the model replied "I don't know" (e.g. q29 breach deadlines, q36, q46, q50).
   The strict "reply exactly ..." prompt makes partial evidence all-or-nothing. -> prompt change:
   answer what the context supports and state what is missing; refuse only when nothing relevant.

Strengths: faithfulness 0.98 (answers stay grounded), 100% correct abstention on out-of-scope questions,
and Azure's built-in prompt shield blocked the "ignore previous instructions" attack (q44) before the model saw it.

## Eval harness caveats (learning-log material)

- Azure content filter returns HTTP 400 for prompt-injection text, including when the *judge* prompt quotes it.
  Blocked generations are scored as refusals; blocked judge calls fall back to phrase matching and are excluded
  from judged averages.
- A prompt-shield block on an answerable adversarial question (q45) counts as a false refusal. That is a real
  trade-off to discuss: the shield protects but can also block legitimate "ignore X" phrasing.
- Section-level recall means any chunk of the right Article counts, even if it is the wrong paragraph.
