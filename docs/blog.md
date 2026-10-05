# I built three kinds of RAG for EU regulation. The fancy one lost.

Written for: engineers and hiring managers who know what RAG is and want evidence, not a tutorial.

I built a compliance assistant over the GDPR, the EU AI Act, NIS2 and DORA, with three pipelines
behind one API: plain hybrid RAG, GraphRAG, and a tool-calling agent. Then I measured them on the
same 60 questions. Code, ADRs and every eval run are in the repository.

## The setup

Azure AI Search for retrieval (BM25 + vectors, fused with RRF), Azure OpenAI for generation, Container
Apps for the API, Terraform for everything. A 60-question set: direct lookups, multi-article,
cross-regulation, out-of-scope, adversarial, ambiguous, and broad "global" questions. An LLM judge
scores answers. Caveat up front: the judge is the same model as the generator, so absolute scores are
optimistic and only large differences mean anything.

## What I measured

| Pipeline | correctness | tokens / question | cost per 1k requests |
|---|---|---|---|
| Hybrid RAG | 0.81 | 2,380 | about $2 |
| GraphRAG (local) | 0.82 | 2,400 | |
| Agent | 0.93 | 3,618 | $3.54 |

The agent wins, and the win is concentrated where plain RAG is weak: cross-regulation questions went
from 0.55 to 0.91. It costs about 1.5x the tokens and 3x the latency. Costs are from small live samples
at prices taken from third-party aggregators, so read them as orders of magnitude.

## What did not work

**The knowledge graph.** I spent a week on entity extraction, a graph and community summaries.
Local graph search matched the baseline (0.82 vs 0.81, inside noise). I gave the agent a graph tool;
across 60 questions it called it zero times. Global search over community summaries scored lower than
plain RAG even on global questions, because summaries drop the exact details the answers need. The
graph did not earn its cost on this corpus.

**The router.** Picking a pipeline per question looked obviously smart. It scored 0.90 against the
agent's 0.93 and was slower, because the classification call is itself an LLM call that eats the saving.

**A semantic cache at 0.95.** I planned a similarity threshold of 0.95, then measured it. Embeddings
put "when is an impact assessment required" and "when is it not required" at 0.90 on median, with some
negatives at 0.96, while true paraphrases averaged 0.92. No threshold separates them. In a legal domain
a wrong cache hit is a confidently wrong answer, so the shipped threshold is 0.97, which is really an
exact-repeat cache, plus a guard that compares article numbers and regulation names, plus a key that
includes what the caller is allowed to see.

## Things that bit me

- A security fix of my own had a bug: an answer ending exactly on a citation, like `[1]`, lost its
  closing bracket. It reached production and was caught only because a new test asserted the exact
  text. Every earlier test ended its sample answer with a period.
- Streaming responses reported zero tokens, so "cost per request" would have been fiction until I asked
  the API to include usage in the stream.
- My first load test mostly measured the cache (308 of 366 requests were hits) and had 14 failures I could
  not reproduce or explain. The second, with unique questions: 242 requests, 0 errors, p95 9.8 s at 10
  concurrent users on one replica. That is all I can claim. Nothing beyond 10 users was tested.

## What I would do next

Measure against a stronger judge from a different model family, test the agent against questions
written to need graph traversal before concluding the graph is useless, move the cache and rate limiter
to a shared store, and replace operator-issued tokens with Entra ID once the tenant allows app
registrations.
