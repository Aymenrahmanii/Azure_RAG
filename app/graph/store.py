"""Knowledge graph storage. `GraphStore` is the interface; the networkx implementation keeps the
whole graph in memory (about 1k sections, a few thousand entities) and persists it as JSON.
Swapping in Neo4j or Cosmos Gremlin means implementing the same methods."""

import json
from pathlib import Path
from typing import Protocol

import networkx as nx


class GraphStore(Protocol):
    def referenced_by(self, section: str) -> list[str]: ...  # sections that cite `section`

    def references(self, section: str) -> list[str]: ...  # sections cited by `section`

    def entities_of(self, section: str) -> set[str]: ...

    def sections_of(self, entity: str) -> set[str]: ...

    def entity_idf(self, entity: str) -> float: ...

    def communities(self) -> list[dict]: ...


def section_key(source: str, section: str) -> str:
    return f"{source}:{section}"  # same format as eval's expected_sections


class NetworkxGraphStore:
    """Nodes: sections ("gdpr:Article 33") and entities ("e:data controller").
    Edges: section -references-> section, section -mentions-> entity, entity -related-> entity."""

    def __init__(self, graph: nx.MultiDiGraph | None = None, communities: list[dict] | None = None):
        self.g = graph if graph is not None else nx.MultiDiGraph()
        self._communities = communities or []
        self._n_sections = sum(1 for _, d in self.g.nodes(data=True) if d.get("kind") == "section")

    # ---- queries -------------------------------------------------------------------------
    def _targets(self, node: str, kind: str, prefix: str = "") -> list[str]:
        if node not in self.g:
            return []
        return [v for _, v, d in self.g.out_edges(node, data=True) if d["kind"] == kind]

    def references(self, section: str) -> list[str]:
        return list(dict.fromkeys(self._targets(section, "references")))

    def referenced_by(self, section: str) -> list[str]:
        if section not in self.g:
            return []
        found = [u for u, _, d in self.g.in_edges(section, data=True) if d["kind"] == "references"]
        return list(dict.fromkeys(found))

    def entities_of(self, section: str) -> set[str]:
        return set(self._targets(section, "mentions"))

    def sections_of(self, entity: str) -> set[str]:
        if entity not in self.g:
            return set()
        return {u for u, _, d in self.g.in_edges(entity, data=True) if d["kind"] == "mentions"}

    def entity_idf(self, entity: str) -> float:
        """Rare entities are informative links; entities in most sections ('regulation') are not."""
        import math

        df = len(self.sections_of(entity))
        return math.log((1 + self._n_sections) / (1 + df)) if df else 0.0

    def communities(self) -> list[dict]:
        return self._communities

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for _, d in self.g.nodes(data=True):
            kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        edges: dict[str, int] = {}
        for _, _, d in self.g.edges(data=True):
            edges[d["kind"]] = edges.get(d["kind"], 0) + 1
        return {"nodes": kinds, "edges": edges, "communities": len(self._communities)}

    # ---- persistence ---------------------------------------------------------------------
    def save(self, path: Path) -> None:
        data = nx.node_link_data(self.g, edges="edges")
        payload = {"graph": data, "communities": self._communities}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "NetworkxGraphStore":
        payload = json.loads(path.read_text(encoding="utf-8"))
        graph = nx.node_link_graph(payload["graph"], edges="edges", multigraph=True, directed=True)
        return cls(graph, payload["communities"])
