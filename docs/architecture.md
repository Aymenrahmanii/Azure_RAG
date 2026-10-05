# Architecture

```mermaid
flowchart LR
    user([User / Streamlit UI]) -->|"POST /chat + bearer token"| api

    subgraph aca["Azure Container Apps (scale 0-3)"]
        api["FastAPI API<br/>auth, rate limit, PII mask,<br/>semantic cache, output sanitiser"]
        api --> agent["Agent (default)<br/>search / get_section / related_sections"]
        api --> base["Baseline RAG<br/>hybrid + RRF"]
    end

    agent --> search
    base --> search
    agent --> llm
    base --> llm
    api -. "query embedding" .-> emb

    search[("Azure AI Search<br/>vector + keyword<br/>(free tier)")]
    llm["Azure OpenAI<br/>gpt-5.4-mini"]
    emb["Azure OpenAI<br/>text-embedding-3-small"]
    graph[("graph.json<br/>Blob container")] --> agent

    subgraph ingest["Ingestion (event driven)"]
        blob[("Blob: documents")] -->|BlobCreated| eg[Event Grid] --> sb["Service Bus queue + DLQ"] --> fn["Function: parse, chunk,<br/>embed, index (idempotent by hash)"]
        fn --> search
        fn --> cosmos[("Cosmos DB<br/>document status")]
    end

    api -. "traces + metrics (OpenTelemetry)" .-> ai[("App Insights /<br/>Log Analytics")]
    mi{{"Managed identity<br/>(no keys in code)"}} -.-> aca
    gh["GitHub Actions<br/>lint, tests, eval gate,<br/>OIDC deploy + approval"] -->|"image push, revision update"| aca
```

## One request

1. Bearer token verified (RS256, issuer, audience, expiry); per-user rate limit checked.
2. PII (IBAN, card, email, phone, IP) is masked in the question before anything else sees it.
3. Visible sources for the caller's groups are computed (security trimming) and set for the request.
4. Semantic cache lookup, keyed by visible sources, mode and k. Hit: replay, no LLM call.
5. Miss: the agent (or baseline) retrieves from AI Search with the trimming filter inside the
   query, calls the LLM, and streams `route`, `step`, `sources`, `token`, `done` events.
6. The answer passes the output sanitiser (no links or images, citations kept), is cached if
   successful, and the request is recorded in the audit log and as metrics.

Everything is built from the same pieces in `app/`: `rag/` (retrieval, agent, cache, router),
`graph/` (knowledge graph), `security/` (auth, access, PII, sanitiser, audit, injection scan),
`providers/` (Azure OpenAI, AI Search), `observability.py`.
