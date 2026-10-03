# EU Regulatory Compliance Assistant: Production RAG on Azure

## Subject
A compliance assistant over **GDPR, EU AI Act, NIS2, DORA** (public, free, ~100-300 documents including recitals, articles, annexes, EDPB/ENISA guidance).

Why this corpus:
- **GraphRAG fits**: articles cross-reference each other, define entities (controller, high-risk system, provider), and reference annexes. Questions like "which obligations apply to a provider of a high-risk AI system that processes health data?" need multi-hop reasoning over relationships.
- **Agentic RAG fits**: questions that need decomposition, comparison across regulations, or a tool call (e.g. "does my system count as high-risk?" -> a checklist tool).
- **Hallucination is costly**: legal answers need exact citations, which forces you to build real guardrails and an "I don't know" path.
- **Realistic enterprise needs**: access control, audit logs, multi-tenancy, cost control.

## Three pipelines behind one interface
| Pipeline | What it is | Best at |
|---|---|---|
| Baseline RAG | Hybrid search (BM25 + vector) + semantic rerank | Direct lookups |
| GraphRAG | Entity/relation extraction -> knowledge graph -> community summaries + graph traversal | Multi-hop, "global" questions |
| Agentic RAG | LLM planner with tools (search, graph query, calculator/checklist), loop with self-critique | Complex, multi-step questions |

A router picks a pipeline per query. Compare all three on the same eval set: quality, latency and cost. That comparison is your main interview material.

## Phases
1. **Local baseline** (wk 1-2): ingest, chunk, embed, Chroma, answer with citations. Eval set of 50-80 questions (easy / multi-hop / unanswerable / adversarial).
2. **Eval harness** (wk 2): recall@k, MRR, nDCG, faithfulness, answer relevance, citation accuracy, abstention rate. LLM-as-judge calibrated against ~20 human-labeled answers. Results tracked in `eval/results/`.
3. **Retrieval tuning** (wk 3): chunking experiments, hybrid, reranking, query rewriting. Experiment table.
4. **Azure foundations + IaC** (wk 4): Terraform, Azure OpenAI, AI Search, Managed Identity, Key Vault. No keys in code.
5. **GraphRAG** (wk 5-6): extraction pipeline, graph store (Neo4j on Container Apps, or Cosmos DB Gremlin), community summaries, local vs global search. Evaluate vs baseline.
6. **Agentic RAG** (wk 6-7): tool-calling loop, planner, step/token budgets, loop guards, tracing every step.
7. **Ingestion pipeline** (wk 7): Blob -> Function -> queue (retries, DLQ) -> parse/chunk/embed/index. Idempotent via content hash, updates and deletes, status in Cosmos DB.
8. **API + deploy** (wk 8): FastAPI streaming `/chat`, Container Apps with autoscaling, Streamlit UI with citations.
9. **Security** (wk 9): Entra ID, JWT, document-level security trimming, prompt-injection defences, content filters, PII handling.
10. **CI/CD + eval gates** (wk 10): GitHub Actions + OIDC, eval in CI blocks regressions, dev -> prod with approval.
11. **Observability, scale, cost** (wk 11): OpenTelemetry -> App Insights, dashboards, semantic cache, load test, cost per 1k queries.
12. **Polish** (wk 12): diagram, ADRs, README, demo, blog. Optional AWS port later.

## Interview questions this project must answer (with evidence)
| Question | Where the answer comes from |
|---|---|
| How did you evaluate your RAG? | Phase 2: retrieval and generation metrics, golden set, calibrated LLM judge, CI gate |
| What if it's used by thousands of users? | Autoscaling (KEDA), async I/O, rate limiting per tenant, semantic cache, queue-based ingestion, AI Search replicas/partitions, Azure OpenAI PTU vs PAYG, 429 backoff, load test results |
| What if the LLM hallucinates? | Grounded prompts, mandatory citations, citation verification, faithfulness check, confidence threshold -> abstain, eval on unanswerable questions |
| What if it's slow? | Latency budget per stage, streaming, parallel retrieval, caching, smaller model for routing/rewrite, reranker top-k tuning, timeouts, p50/p95/p99 dashboards |
| Why GraphRAG / Agentic over plain RAG? | Your three-way comparison table, including cost and latency |
| How do you handle document updates? | Hash-based idempotent ingestion, update/delete propagation to index and graph |
| How do you secure it? | Entra ID, security trimming, prompt-injection defences, managed identity, private endpoints |
| How do you control cost? | Token budgets, caching, model routing, per-request cost metric |
| How do you do safe releases? | CI eval gate, IaC, dev/prod, canary or revision-based rollout in Container Apps |
| What happens when a dependency fails? | Retries, circuit breaker, fallbacks, degraded mode, DLQ |
| How do you monitor quality in production? | Feedback endpoint, sampled online evals, drift dashboards |

## Principles
- Interfaces first: `LLMProvider`, `Embedder`, `VectorStore`, `GraphStore`, `Retriever`.
- Prove each change with a number on the eval set. No change merges without one.
- Keep `docs/learning-log.md` and short ADRs as you go.
- Finish local retrieval quality before touching Azure.
