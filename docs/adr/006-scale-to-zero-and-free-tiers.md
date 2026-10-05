# 006: Scale to zero and free tiers over always-on capacity

**Status:** accepted (2026-10-03)

**Context.** The project runs on a student subscription with a small credit.

**Decision.** Container Apps on the consumption plan with `min_replicas = 0` (max 3, HTTP concurrency
rule), AI Search on the free tier, pay-as-you-go Azure OpenAI, Log Analytics with a daily cap.

**Consequences.** Idle cost is roughly the container registry (about $5/month). The first request after
idle pays a cold start (measured 4.3 s; the BM25 index is built at startup). The free Search tier has
no replicas or SLA, so the answer to "what about thousands of users" is the design (autoscaling, rate
limiting, 429 backoff, cache), not a demonstrated result: the real load test is still to do.
