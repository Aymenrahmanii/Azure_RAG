import asyncio

import networkx as nx

from app.graph.build import build_structure, detect_communities
from app.graph.extract import normalize, parse_extraction
from app.graph.search import GlobalSearch
from app.graph.store import NetworkxGraphStore
from app.providers.base import Chunk


def test_normalize_entity_names():
    assert normalize("  The Data Controllers. ") == "data controller"
    assert normalize("Address") == "address"  # no mangling of -ss
    assert normalize("high-risk AI systems") == "high-risk ai system"


def test_parse_extraction_tolerates_fences_and_drops_dangling_relations():
    raw = """```json
    {"entities": [{"name": "Controller", "type": "actor", "description": "d"},
                  {"name": "Supervisory Authorities", "type": "authority"}],
     "relations": [{"source": "controller", "target": "supervisory authority",
                    "relation": "notifies"},
                   {"source": "controller", "target": "ghost", "relation": "x"}]}
    ```"""
    out = parse_extraction(raw)
    assert [e["name"] for e in out["entities"]] == ["controller", "supervisory authority"]
    assert out["relations"] == [
        {"source": "controller", "target": "supervisory authority", "relation": "notifies"}
    ]


def chunk(doc, section, text):
    meta = {"source": doc, "section": section, "title": "t", "chapter": "", "part": 0}
    return Chunk(f"{doc}:{section}:0", doc, text, meta)


def test_build_structure_links_references_and_entities_and_roundtrips(tmp_path):
    chunks = [
        chunk("gdpr", "Article 33", "Notify as set out in Article 34 of this Regulation."),
        chunk("gdpr", "Article 34", "Communicate to the data subject."),
    ]
    ext = {
        "gdpr:Article 33:0": {
            "entities": [{"name": "breach", "type": "concept", "description": "d"}],
            "relations": [],
        },
        "gdpr:Article 34:0": {
            "entities": [{"name": "breach", "type": "concept", "description": "d"}],
            "relations": [],
        },
    }
    g = build_structure(chunks, ext)
    store = NetworkxGraphStore(
        g, [{"id": 0, "title": "x", "summary": "y", "entities": [], "sections": []}]
    )
    assert store.references("gdpr:Article 33") == ["gdpr:Article 34"]
    assert store.sections_of("e:breach") == {"gdpr:Article 33", "gdpr:Article 34"}
    path = tmp_path / "g.json"
    store.save(path)
    again = NetworkxGraphStore.load(path)
    assert again.references("gdpr:Article 33") == ["gdpr:Article 34"]
    assert again.communities()[0]["title"] == "x"


def test_detect_communities_groups_cooccurring_entities(monkeypatch):
    monkeypatch.setattr("app.graph.build.RESOLUTION", 1.0)  # perfect cliques split at high values
    g = nx.MultiDiGraph()
    for i in range(100):  # many sections so the hub filter does not remove everything
        g.add_node(f"d:S{i}", kind="section")
    for group, names in (("a", "abcdef"), ("b", "ghijkl")):
        for s in range(5):
            for n in names:
                g.add_node(f"e:{n}", kind="entity", name=n, type="concept", description="")
                g.add_edge(f"d:S{s + (0 if group == 'a' else 20)}", f"e:{n}", kind="mentions")
    comms = detect_communities(g)
    assert len(comms) == 2
    assert {frozenset(c["entities"]) for c in comms} == {
        frozenset(f"e:{n}" for n in "abcdef"),
        frozenset(f"e:{n}" for n in "ghijkl"),
    }


class EchoLLM:
    async def generate(self, system, user):
        return "ok " + user.splitlines()[1][:20]


def test_global_search_ranks_relevant_community_first():
    comms = [
        {
            "id": 0,
            "title": "Breach notification",
            "summary": "notify supervisory authority of a personal data breach",
            "entities": ["breach"],
            "sections": ["gdpr:Article 33"],
        },
        {
            "id": 1,
            "title": "AI sandboxes",
            "summary": "regulatory sandboxes for testing AI systems",
            "entities": ["sandbox"],
            "sections": ["eu_ai_act:Article 57"],
        },
    ]
    gs = GlobalSearch(EchoLLM(), NetworkxGraphStore(nx.MultiDiGraph(), comms), top_communities=1)
    answer = asyncio.run(gs.ask("who must be notified of a data breach?"))
    assert answer.sources[0].chunk.metadata["section"] == "Community 0"
