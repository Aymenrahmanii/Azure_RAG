# 001: Agentic RAG is the default pipeline

**Status:** accepted (2026-10-04)

**Context.** Three pipelines answer the same questions: baseline hybrid RAG, GraphRAG local search,
and a tool-calling agent. A router can pick one per question.

**Decision.** `/chat` defaults to the agent. `mode=baseline|graph|global|auto` stay available.

**Evidence** (60 questions, [experiments.md](../experiments.md), runs 16-21): correctness 0.93 for
the agent vs 0.81 baseline; cross-regulation questions 0.55 -> 0.91; global 0.66 -> 0.83. Cost is
1.5x tokens and about 3x latency. The router (0.90) added a classification call whose latency ate
the saving, so it did not beat always-agent.

**Consequences.** Slower and costlier per query (measured $3.54 vs about $2 per 1k requests,
[observability.md](../observability.md)); broad questions can use 3-4x the average tokens, bounded by
a 60k-token budget and a loop guard. Revisit the router if a fast non-reasoning classifier is used.
The agent's prompt fix was tuned on a question in the eval set, so 0.93 is slightly optimistic.
