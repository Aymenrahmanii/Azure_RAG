# Experiment log

Every change to the pipeline gets a row here, with the eval run that proves it.
Run: `python -m eval.run --label <name>` (add `--no-generate` for free retrieval-only runs).
Judge and generator are the same model (gpt-5.4-mini), so judge scores are optimistic; trends matter more than absolutes.

| # | Label | Change | recall@5 | MRR | recital share | faithfulness | correctness | citation prec. | false refusals | correct abstention | p50 / p95 latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | MiniLM embeddings, article-aware chunks (max 1500 chars), top-5 dense, strict "I don't know" prompt | 0.73 | 0.72 | 0.35 | 0.98 | 0.75 | 0.71 | 0.29 | 1.00 | 2.9 s / 11.0 s |
| 1 | r1-no-recitals | + exclude recitals from retrieval | 0.78 | 0.81 | 0.00 | | | | | | |
| 2 | r2-hybrid | dense + BM25 fused with RRF (recitals kept) | 0.74 | 0.72 | 0.39 | | | | | | |
| 3 | r3-hybrid-no-recitals | hybrid + exclude recitals | 0.86 | 0.87 | 0.00 | | | | | | |
| 4 | r4-hybrid-no-recitals-rerank | + cross-encoder rerank (ms-marco-MiniLM) of 30 candidates | 0.93 | 0.93 | 0.00 | | | | | | |
| 5 | r5-bge-small | r4 with BAAI/bge-small-en-v1.5 embeddings | 0.94 | 0.95 | 0.00 | | | | | | |
| 6 | r6-candidates-60 | r4 with 60 candidates | 0.93 | 0.94 | 0.00 | | | | | | |
| 7 | g-strict | r5 retrieval + original strict prompt (zero-shot) | 0.94 | 0.95 | 0.00 | 1.00 | 0.83 | 0.94 | 0.12 | 1.00 | 3.5 s / 10.8 s |
| 8 | g-balanced | r5 retrieval + "answer what is supported" prompt (zero-shot) | 0.94 | 0.95 | 0.00 | 0.99 | 0.88 | 0.91 | 0.00 | 0.89 | 4.5 s / 10.4 s |
| 9 | **g-fewshot** | r5 retrieval + balanced prompt + 3 few-shot examples | 0.94 | 0.95 | 0.00 | 1.00 | 0.88 | 0.89 | 0.02 | 1.00 | 2.9 s / 10.3 s |
| 10 | az-fewshot | **Azure**: AI Search (vector store) + Entra-token auth to Azure OpenAI, same pipeline as run 9 | 0.94 | 0.95 | 0.00 | 1.00 | 0.85 | 0.91 | 0.05 | 1.00 | 3.1 s / 11.0 s |

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

## Retrieval and prompt findings (runs 1-9)

- **Retrieval, recall@5 0.73 -> 0.94.** No single change did it. Dropping recitals alone gave +0.05, hybrid alone
  gave +0.01 (BM25 pulled in more recitals), but hybrid + no recitals gave +0.13, and the cross-encoder
  reranker added +0.07 more. Components interact, so ablations must be run in combination.
- **Bigger candidate pool (30 -> 60) and a stronger embedding model (MiniLM -> bge-small) were marginal**
  (+0.00 / +0.01). Diminishing returns: stop tuning retrieval and look at what is left.
- **Prompting, zero-shot vs few-shot.** The strict prompt over-refused (false refusals 0.12). The balanced
  zero-shot prompt removed refusals but then answered one question it should have refused (correct abstention
  1.00 -> 0.89). Three few-shot examples (a partial answer, a false-premise correction, a refusal) kept both:
  false refusals 0.02 and abstention 1.00. Correctness 0.88 vs 0.83 for strict.
- **Caveat: single runs, 50 questions, LLM judge.** Differences of ~0.05 are inside the noise. The retrieval gains
  and the false-refusal/abstention trade-off are large enough to trust; strict vs few-shot correctness is not.
- Recitals are excluded entirely. That is a deliberate trade-off: a question about legislative intent would
  not find them. Revisit with a recital-specific route if needed.
- Remaining weak spot: multi-article questions (recall@5 0.77): the answer spans 2-3 articles; hypothesis (not yet
  verified): top-5 slots are taken by several chunks of the first article. Candidates: diversity (MMR), a larger k for
  multi-part questions, or query decomposition (an agentic RAG use case).

## Week 4: local vs Azure (run 10)

Moving the vector store to Azure AI Search (free tier) and authenticating with Entra ID instead of an API key
left retrieval **identical** (recall@5 0.94, MRR 0.95: same embeddings, same hybrid + rerank logic, which runs in
the app). Generation moved within noise (correctness 0.85 vs 0.88, false refusals 0.05 vs 0.02, p50 3.1 s vs 2.9 s).
Conclusion: the provider abstraction works; the migration changed infrastructure, not quality.
Not yet tried: AI Search native hybrid / semantic ranker (needs a paid tier) and Azure OpenAI embeddings.
