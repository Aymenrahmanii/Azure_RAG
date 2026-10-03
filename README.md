# Azure RAG: EU Regulatory Compliance Assistant

A production-style RAG system over GDPR, the EU AI Act, NIS2 and DORA. It implements baseline hybrid RAG, GraphRAG and Agentic RAG behind one interface, evaluates them on the same question set, and deploys them on Azure with Terraform and GitHub Actions.

Plan and rationale: [docs/PLAN.md](docs/PLAN.md). Status: week 1 (local prototype).

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
cp .env.example .env
python scripts/download_corpus.py
pytest
```
