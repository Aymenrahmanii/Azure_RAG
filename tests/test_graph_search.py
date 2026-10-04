import networkx as nx

from app.graph.search import GraphConfig, GraphRetriever
from app.graph.store import NetworkxGraphStore
from app.providers.base import Chunk, RetrievedChunk


def chunk(source: str, section: str) -> Chunk:
    return Chunk(
        f"{source}:{section}:0",
        source,
        f"text of {section}",
        {"source": source, "section": section, "title": "", "chapter": "", "part": 0},
    )


class FakeBase:
    """Stands in for the hybrid Retriever: fixed ranking, flat BM25 scores."""

    def __init__(self, chunks, ranking):
        self.chunks, self._ranking = chunks, ranking

    def retrieve(self, query, k):
        return [
            RetrievedChunk(self.chunks[i], 1.0 / (n + 1)) for n, i in enumerate(self._ranking[:k])
        ]

    def bm25_scores(self, query):
        return [0.0] * len(self.chunks)


def make_graph(n_filler: int = 20) -> NetworkxGraphStore:
    g = nx.MultiDiGraph()
    names = ["gdpr:Article 33", "gdpr:Article 34", "nis2:Article 23", "dora:Article 5"]
    for n in names + [f"gdpr:Filler {i}" for i in range(n_filler)]:
        g.add_node(n, kind="section")
    g.add_edge("gdpr:Article 33", "gdpr:Article 34", kind="references")
    # rare entity shared by two sections in different regulations
    g.add_node("e:incident notification", kind="entity")
    for s in ("gdpr:Article 33", "nis2:Article 23"):
        g.add_edge(s, "e:incident notification", kind="mentions")
    # generic entity present everywhere: must not create links
    g.add_node("e:regulation", kind="entity")
    for n in list(g.nodes):
        if g.nodes[n]["kind"] == "section":
            g.add_edge(n, "e:regulation", kind="mentions")
    return NetworkxGraphStore(g)


def test_graph_queries_and_idf():
    graph = make_graph()
    assert graph.references("gdpr:Article 33") == ["gdpr:Article 34"]
    assert graph.referenced_by("gdpr:Article 34") == ["gdpr:Article 33"]
    assert graph.entity_idf("e:incident notification") > graph.entity_idf("e:regulation")


def test_expansion_prefers_references_then_rare_entities_and_ignores_generic():
    graph = make_graph()
    chunks = [chunk("gdpr", "Article 33"), chunk("gdpr", "Article 34"), chunk("nis2", "Article 23")]
    r = GraphRetriever(FakeBase(chunks, [0]), graph, GraphConfig(min_idf=1.0))
    seeds = [RetrievedChunk(chunks[0], 1.0)]
    ranked = r.expand(seeds, exclude={"gdpr:Article 33"})
    assert [s for s, _ in ranked] == ["gdpr:Article 34", "nis2:Article 23"]  # no generic hub


def test_retrieve_fills_k_with_base_then_graph_sections():
    graph = make_graph()
    chunks = [
        chunk("gdpr", "Article 33"),
        chunk("dora", "Article 5"),
        chunk("gdpr", "Article 34"),
        chunk("nis2", "Article 23"),
    ]
    r = GraphRetriever(
        FakeBase(chunks, [0, 1]), graph, GraphConfig(n_graph=2, seeds=1, min_idf=1.0)
    )
    out = r.retrieve("breach", 3)
    assert [c.chunk.metadata["section"] for c in out] == ["Article 33", "Article 34", "Article 23"]


def test_no_graph_slots_returns_plain_base():
    graph = make_graph()
    chunks = [chunk("gdpr", "Article 33"), chunk("gdpr", "Article 34")]
    r = GraphRetriever(FakeBase(chunks, [0, 1]), graph, GraphConfig(n_graph=0))
    assert len(r.retrieve("q", 2)) == 2
