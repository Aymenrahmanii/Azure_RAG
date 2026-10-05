# 005: Semantic cache at 0.97, in process

**Status:** accepted (2026-10-05)

**Context.** Repeated questions should not pay for retrieval and LLM calls. A wrong cache hit on a
legal question is a confidently wrong answer.

**Decision.** In-process cache (TTL 1 h, 500 entries) keyed by visible sources, mode and k; a hit needs
cosine >= 0.97 and identical anchors (numbers, regulation names); only successful, non-filtered
answers are stored.

**Evidence** (`python -m eval.cache_threshold`): paraphrases (median 0.92) and hard negatives such as
"required" vs "not required" (median 0.90, max 0.96) overlap, so no threshold separates them; 0.97 had 0
false hits on 6 negatives and 206 distinct-question pairs, at the cost of hitting 0 of 10 paraphrases.
Small sample.

**Consequences.** It is an exact-repeat cache, not a paraphrase cache; the real hit rate is unknown
until real traffic exists. Each replica has its own cache (a shared Redis cache is the production
answer; the interface would not change). Paraphrase caching would need a verification step.
