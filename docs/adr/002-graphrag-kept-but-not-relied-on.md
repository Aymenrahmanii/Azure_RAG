# 002: GraphRAG is built and shipped, but not relied on

**Status:** accepted (2026-10-04)

**Context.** The plan assumed a knowledge graph (entities, cross-references, communities) would help
multi-hop and global questions.

**Decision.** Keep the graph (built offline, loaded from Blob at startup) and the `graph`/`global`
modes, but do not make either the default.

**Evidence.** Local graph search matched the baseline (0.82 vs 0.81, inside judge noise). The agent
was given `related_sections` and called it 0 times in 60 questions. Global-only search scored 0.59
vs 0.66 for plain RAG and refused 41% of answerable questions.

**Consequences.** The graph did not earn its build cost on this corpus and question set. Reasons are
untested (citation lookup is already covered by `get_section`; few questions need multi-hop
traversal). A corpus with denser entity relationships, or questions designed for traversal, could
change this. It stays as a documented negative result.
