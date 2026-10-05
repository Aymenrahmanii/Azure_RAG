# 003: Hybrid retrieval, recitals excluded, reranker off in the cloud image

**Status:** accepted (2026-10-03/04)

**Decision.** Dense + BM25 fused with RRF, recitals excluded from retrieval, no cross-encoder in the
deployed image (`RETRIEVAL_RERANK=false`).

**Evidence** ([experiments.md](../experiments.md)): recall@5 0.73 (dense baseline) -> 0.78 (no
recitals) -> 0.86 (hybrid, no recitals). Reranking took local runs to 0.93-0.94 with weak MiniLM
embeddings; with Azure text-embedding-3-small the reranker adds nothing measurable (recall@5 0.93 vs
0.94, run 12).

**Consequences.** The container image stays small (no PyTorch). Excluding recitals trades
interpretive context for precision: questions that need recitals will not find them. BM25 is built in
memory at startup from every chunk in AI Search, so startup time grows with the corpus and index
updates are not visible until restart.
