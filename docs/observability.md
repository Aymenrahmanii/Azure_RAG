# Observability, cache and cost (phase 11)

## What is instrumented

OpenTelemetry via the Azure Monitor distro (`app/observability.py`), exported to the existing
Application Insights resource. Without `APPLICATIONINSIGHTS_CONNECTION_STRING` everything is a no-op.

- **Traces**: every `/chat` request (auto-instrumented), plus our spans `rag.cache.lookup`,
  `rag.retrieve.dense|sparse`, `rag.llm.chat|generate|stream`, `rag.agent.tool` (attribute `tool`),
  and the calls under them (Azure OpenAI, AI Search, managed-identity token).
- **Metrics** (low cardinality: pipeline, status, cached, direction, result; never user or question):
  `rag.requests`, `rag.request.duration`, `rag.ttft`, `rag.tokens`, `rag.llm.calls`,
  `rag.cost.usd`, `rag.cache`, `rag.stage.duration`.
- Health probes are excluded (`OTEL_PYTHON_EXCLUDED_URLS=healthz`).

Queries (verified against the live workspace) are in [docs/kql/](kql/): latency percentiles, stage
breakdown, cost per 1k requests, cache hit rate and errors, agent tool usage. Run them in the
Log Analytics workspace `log-azrag-dev` (tables `AppRequests`, `AppDependencies`, `AppMetrics`).
There is no saved workbook yet: the queries are the dashboard source.

Cost is `tokens x price`, prices from settings (`PRICE_INPUT_PER_MTOK` 0.75, `PRICE_OUTPUT_PER_MTOK`
4.50 USD per million for gpt-5.4-mini, as listed by third-party price aggregators in June 2026, not
by Azure's own sheet: verify before trusting the dollar figures). Embedding and Search costs are
not in the estimate.

## Semantic cache (`app/rag/cache.py`)

In-process, per replica, TTL 1 h, max 500 entries. A hit skips retrieval and every LLM call.
Safety: key includes visible sources (security trimming), mode and k; hit also requires identical
"anchors" (numbers, regulation names); only successful non-filtered answers are stored.

**Threshold, measured** (`python -m eval.cache_threshold`, text-embedding-3-small, 10 paraphrase
pairs, 6 hard negatives, 206 distinct eval-question pairs sharing anchors):

| threshold | paraphrases hit | hard-negative false hits | distinct false hits |
|---|---|---|---|
| 0.90 | 9/10 | 3/6 | 1 |
| 0.95 | 1/10 | 1/6 | 0 |
| 0.97 | 0/10 | 0/6 | 0 |

Paraphrase and hard-negative similarities overlap (median 0.92 vs 0.90; negatives like "required"
vs "not required" reach 0.96), so **no threshold separates them**. Default is 0.97: effectively an
exact-repeat cache (retries, FAQ-style repeats), not a paraphrase cache. Small sample: indicative,
not a guarantee. Live check: identical question 0.09 s vs 3.45 s; Article 5 vs Article 6 correctly
missed; a lowercased, unpunctuated repeat missed. Real-world hit rate is unknown until real
traffic exists. Making paraphrase caching safe would need a verification step (e.g. a small model
judging "same question?"), which costs a call.

## Cost per 1k queries (measured, deployed v5, 2026-10-05, small samples)

| Pipeline | Requests | Tokens/request | LLM calls/request | Cost per 1k requests |
|---|---|---|---|---|
| agent (default) | 8 | 3,498 (3,253 in, 245 out) | 2.1 | **$3.54** |
| baseline (fresh) | 3 | ~1,960 | 1 | **~$2.00** |
| cache hit | 1 | 0 | 0 | ~$0 (0.09 s) |

At hit rate h the LLM cost is roughly `(1 - h) x` the figure above. Fixed cost is mostly the
container registry (~$5/month); Container Apps scales to zero, AI Search is on the free tier. At the
agent price, the registry fee equals the LLM cost of about 1,400 queries. Eight agent requests is
a tiny sample: token counts vary by question, so treat the figures as order of magnitude.

## Load test (`python -m eval.loadtest`)

Closed-loop virtual users with one token each (the rate limit is per user). Only a smoke run has
been done so far (2 users, 30 s, agent, 8 requests, 0 errors, p50 4.2 s, p95 6.6 s): that
shows the tool works, **not** how the system behaves at scale. A real run (autoscaling to 3
replicas, Azure OpenAI 429s, rate limiter) is still to do; it costs about $0.35 per 100 agent
requests.
