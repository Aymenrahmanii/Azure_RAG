# Azure RAG: EU Regulatory Compliance Assistant

A production-style RAG system over the GDPR, the EU AI Act, NIS2 and DORA. It implements baseline hybrid RAG, GraphRAG and Agentic RAG behind one API, evaluates them on the same 60 questions, and runs on Azure with Terraform, GitHub Actions, authentication, security trimming and observability.

Status: phases 1-11 done and deployed (the real load test is still to do), phase 12 (polish) in progress. Plan: [docs/PLAN.md](docs/PLAN.md).

Architecture diagram and the path of one request: [docs/architecture.md](docs/architecture.md).

## What the evaluation found

60 questions, Azure AI Search, text-embedding-3-small, gpt-5.4-mini ([full tables](docs/experiments.md)). The judge is the same model as the generator, so absolute scores are optimistic and trends matter more.

| Pipeline | correctness | tokens / q | p50 latency | cost / 1k requests |
|---|---|---|---|---|
| Baseline hybrid RAG | 0.81 | 2,380 | 3.9 s | about $2 |
| GraphRAG (local search) | 0.82 | 2,400 | 3.6 s | |
| **Agentic RAG (default)** | **0.93** | 3,618 | 12.4 s | $3.54 |
| Router (auto) | 0.90 | 4,019 | 12.4 s | |

Latencies are under concurrency 4 on a rate-limited deployment (a single user sees less); costs are from small live samples at assumed prices ([observability.md](docs/observability.md)). Retrieval tuning took recall@5 from 0.73 to 0.94.

Results that went against the plan, kept in the docs on purpose:
- The knowledge graph did not help: the agent never called its graph tool, and GraphRAG matched the baseline ([ADR 002](docs/adr/002-graphrag-kept-but-not-relied-on.md)).
- The router did not pay for itself ([ADR 001](docs/adr/001-agent-by-default.md)).
- Embeddings cannot tell "required" from "not required", so the semantic cache is near-exact only ([ADR 005](docs/adr/005-near-exact-semantic-cache.md)).
- Prompt injection through retrieved passages: 1.4% attack success on a plain prompt, 0 of 240 for the agent ([security.md](docs/security.md)).

## Documentation

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Diagram and request flow |
| [docs/adr/](docs/adr/README.md) | Decisions and the evidence behind them |
| [docs/experiments.md](docs/experiments.md) | Every eval run and what it showed |
| [docs/security.md](docs/security.md) | Controls, known limits, injection results |
| [docs/cicd.md](docs/cicd.md) | Pipeline design and one-time GitHub setup |
| [docs/observability.md](docs/observability.md) | Telemetry, cache, cost per 1k queries, load test |
| [docs/learning-log.md](docs/learning-log.md) | Problems hit and how they were solved |

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[local,dev]"
pre-commit install
cp .env.example .env
python scripts/download_corpus.py
pytest
```

## Run the API and UI

```bash
uvicorn app.api.main:app --port 8000      # POST /chat streams Server-Sent Events
streamlit run ui/streamlit_app.py         # chat UI with citations (API_URL env var, default localhost:8000)
```

The API requires a bearer token outside local development (`python -m app.security.mint`, see [docs/security.md](docs/security.md)). Deploy to Container Apps: the flow is at the top of [infra/app.tf](infra/app.tf). Images are built locally (`Dockerfile`, `Dockerfile.ui`) and pushed to ACR.

## Evaluate, tune, load test

```bash
python -m eval.run --label my-change      # eval harness (--no-generate for free retrieval-only runs)
python -m eval.injection                  # prompt-injection resistance
python -m eval.cache_threshold            # semantic cache threshold
python -m eval.loadtest --url <api> --key .keys/private.pem --users 5 --duration 60   # spends real money
```

## CI/CD

Pull requests run lint, tests, Terraform validation, image builds and an eval gate that fails when answer quality drops below `eval/thresholds.json`; `main` deploys behind a manual approval with automatic rollback. The GitHub-side setup (variables, `production` environment, branch protection) is a one-time manual step: [docs/cicd.md](docs/cicd.md).

## Known limits

Self-issued tokens instead of Entra ID, per-replica rate limit and cache, no VNet/private endpoints, free-tier AI Search (no SLA), a judge that shares the generator's model, and no real load test yet. Details in [docs/security.md](docs/security.md) and the ADRs.
