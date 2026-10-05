# Architecture decision records

Short records of decisions that cost something to reverse, each with the evidence behind it.

| # | Decision |
|---|---|
| [001](001-agent-by-default.md) | Agentic RAG is the default pipeline |
| [002](002-graphrag-kept-but-not-relied-on.md) | GraphRAG is built and shipped, but not relied on |
| [003](003-hybrid-retrieval-without-reranker-in-cloud.md) | Hybrid retrieval, recitals excluded, reranker off in the cloud image |
| [004](004-self-issued-jwt.md) | Self-issued RS256 tokens instead of Entra ID |
| [005](005-near-exact-semantic-cache.md) | Semantic cache at 0.97, in process |
| [006](006-scale-to-zero-and-free-tiers.md) | Scale to zero and free tiers over always-on capacity |
| [007](007-eval-gate-in-ci.md) | Eval thresholds gate the build |
