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

Closed-loop virtual users, one token each (the rate limit is per user), 2 s mean think time, against
the deployed API (v5, one Container App, scale 0-3, free-tier AI Search, gpt-5.4-mini).

**Run B, the one that counts** (10 users, 180 s, agent mode, every question unique so the cache cannot
answer; results in `eval/results/loadtest-10u-3m-unique-v5.json`):

| Requests | Errors | Throughput | p50 | p95 | p99 | Tokens/request | Replicas |
|---|---|---|---|---|---|---|---|
| 242 | 0 | 1.3 req/s | 4.7 s | 9.8 s | 12.2 s | 3,688 | 1 (cold start from 0 during the test, no errors) |

Reading it: with 10 concurrent users the system stayed at one replica (the scale rule is 10
concurrent requests per replica, so this sits right at the threshold) and latency was dominated by
LLM calls, not by the API. No 429 from Azure OpenAI or the rate limiter appeared. It does **not**
show behaviour beyond about 10 concurrent users: autoscaling to 3 replicas and Azure OpenAI quota
limits were not reached, so "thousands of users" remains an untested claim. Cost of the run was
about $0.9 at the assumed prices.

**Run A, a flawed first attempt, kept because of what it showed** (same settings, questions drawn
from a pool of 36, 366 requests): 308 were cache hits (p50 1.4 s), so it mostly measured the cache,
which is the cache working as designed, but it is not a capacity result. It also had 14 failures
(8 x HTTP 408, 5 transport errors, 1 x 429) that did **not** reproduce in run B. I could not establish
the cause: the server recorded no errors for them. A scale-out or cold-start effect is a
hypothesis only, and run B's clean cold start argues against it being a general problem.

Telemetry caveat found on the way: the Azure Monitor distro samples traces (about 5 per second by
default), so `AppRequests` counts are lower than the real request count (229 recorded vs 366 sent).
The `rag.*` metrics are not sampled and matched the client exactly, so use metrics for counts and
traces for latency shape.
