# Azure RAG: EU Regulatory Compliance Assistant

A production-style RAG system over GDPR, the EU AI Act, NIS2 and DORA. It implements baseline hybrid RAG, GraphRAG and Agentic RAG behind one interface, evaluates them on the same question set, and deploys them on Azure with Terraform and GitHub Actions.

Plan and rationale: [docs/PLAN.md](docs/PLAN.md). Status: week 1 (local prototype).

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
uvicorn app.api.main:app --port 8000      # POST /chat streams Server-Sent Events: sources, token*, done
streamlit run ui/streamlit_app.py         # chat UI with citations (API_URL env var, default localhost:8000)
```

Deploy to Container Apps (API and UI): see the flow at the top of [infra/app.tf](infra/app.tf). Images are built locally (`Dockerfile`, `Dockerfile.ui`) and pushed to ACR.
