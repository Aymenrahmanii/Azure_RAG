"""Build the knowledge graph from the indexed chunks.

python -m app.graph.build                 # full build (LLM extraction is cached)
python -m app.graph.build --limit 40      # smoke test on the first 40 chunks
python -m app.graph.build --no-summaries  # skip community summaries
"""

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import networkx as nx

from app.core.config import settings
from app.graph.extract import Extractor, normalize
from app.graph.refs import extract_refs, regulation_mentions
from app.graph.store import NetworkxGraphStore, section_key
from app.providers.base import Chunk
from app.providers.factory import make_embedder, make_llm, make_store
from app.providers.openai_compat import ContentFiltered

GRAPH_PATH = Path("data/processed/graph.json")
EXTRACTIONS_PATH = Path("data/processed/extractions.jsonl")
SUMMARIES_PATH = Path("data/processed/community_summaries.jsonl")
HUB_SHARE = 0.08  # entities in more than 8% of sections are too generic to define communities
MIN_COMMUNITY = 5
RESOLUTION = 2.5  # higher = more, smaller communities (1.0 gave 18, one of 525 entities)

SUMMARY_SYSTEM = """You summarise one cluster of related concepts from EU regulations
(GDPR, AI Act, NIS2, DORA). Write a short title on the first line, then 3-5 sentences saying what
the cluster is about, which obligations or rights it covers, and which regulations it spans.
Use only the information given. Do not invent article numbers."""


def build_structure(chunks: list[Chunk], extractions: dict[str, dict]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    by_section: dict[str, list[Chunk]] = defaultdict(list)
    for c in chunks:
        by_section[section_key(c.metadata["source"], c.metadata["section"])].append(c)
    for key, cs in by_section.items():
        m = cs[0].metadata
        g.add_node(key, kind="section", source=m["source"], label=m["section"], title=m["title"])

    # deterministic cross-references and explicit mentions of other regulations
    for key, cs in by_section.items():
        source = cs[0].metadata["source"]
        text = "\n".join(c.text for c in cs)
        for doc, label in extract_refs(text, source):
            target = section_key(doc, label)
            if target != key and target in g:
                g.add_edge(key, target, kind="references")
        for doc in regulation_mentions(text, source):
            g.add_node(f"doc:{doc}", kind="doc")
            g.add_edge(key, f"doc:{doc}", kind="mentions_regulation")

    # LLM entities and relations
    desc: dict[str, str] = {}
    for c in chunks:
        ext = extractions.get(c.id, {})
        key = section_key(c.metadata["source"], c.metadata["section"])
        for e in ext.get("entities", []):
            e = {**e, "name": normalize(e["name"])}  # idempotent; merges older cached spellings
            node = f"e:{e['name']}"
            if node not in g:
                g.add_node(node, kind="entity", name=e["name"], type=e["type"], description="")
            desc.setdefault(node, e["description"])
            if not g.has_edge(key, node):
                g.add_edge(key, node, kind="mentions")
        for r in ext.get("relations", []):
            src, dst = normalize(r["source"]), normalize(r["target"])
            if src != dst and f"e:{src}" in g and f"e:{dst}" in g:
                g.add_edge(f"e:{src}", f"e:{dst}", kind="related", label=r["relation"])
    for node, d in desc.items():
        g.nodes[node]["description"] = d
    return g


def detect_communities(g: nx.MultiDiGraph) -> list[dict]:
    """Louvain on the entity co-occurrence graph (entities that appear in the same section)."""
    sections = [n for n, d in g.nodes(data=True) if d["kind"] == "section"]
    store = NetworkxGraphStore(g)
    hub_limit = HUB_SHARE * len(sections)
    co = nx.Graph()
    for s in sections:
        ents = [e for e in store.entities_of(s) if len(store.sections_of(e)) <= hub_limit]
        for i, a in enumerate(ents):
            for b in ents[i + 1 :]:
                w = co.get_edge_data(a, b, {}).get("weight", 0)
                co.add_edge(a, b, weight=w + 1)
    for u, v, d in g.edges(data=True):  # explicit relations also bind entities
        if d["kind"] == "related" and u in co and v in co:
            co[u][v]["weight"] = co[u][v].get("weight", 0) + 2
    parts = nx.community.louvain_communities(co, weight="weight", resolution=RESOLUTION, seed=42)
    out = []
    for members in sorted(parts, key=len, reverse=True):
        if len(members) < MIN_COMMUNITY:
            continue
        counts: dict[str, int] = defaultdict(int)
        for e in members:
            for s in store.sections_of(e):
                counts[s] += 1
        top_sections = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:12]]
        out.append(
            {
                "id": len(out),
                "entities": sorted(members),
                "sections": top_sections,
                "title": "",
                "summary": "",
            }
        )
    return out


def _community_prompt(g: nx.MultiDiGraph, com: dict) -> str:
    members = set(com["entities"])
    ranked = sorted(members, key=lambda e: -g.degree(e))[:15]
    lines = [f"- {g.nodes[e]['name']}: {g.nodes[e]['description']}" for e in ranked]
    rels = [
        f"- {g.nodes[u]['name']} {d['label']} {g.nodes[v]['name']}"
        for u, v, d in g.edges(data=True)
        if d["kind"] == "related" and u in members and v in members
    ][:20]
    secs = ", ".join(com["sections"][:10])
    return (
        "Concepts:\n"
        + "\n".join(lines)
        + "\n\nRelations:\n"
        + "\n".join(rels)
        + f"\n\nSections: {secs}"
    )


async def summarise(llm, g: nx.MultiDiGraph, communities: list[dict], concurrency: int = 6) -> None:
    cache: dict[str, dict] = {}
    if SUMMARIES_PATH.exists():
        for line in SUMMARIES_PATH.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            cache[rec["key"]] = rec
    sem = asyncio.Semaphore(concurrency)

    async def one(com: dict) -> None:
        prompt = _community_prompt(g, com)
        key = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        if key not in cache:
            async with sem:
                try:
                    raw = await llm.generate(SUMMARY_SYSTEM, prompt)
                except ContentFiltered:
                    raw = "Untitled cluster\n"
            title, _, body = raw.strip().partition("\n")
            cache[key] = {"key": key, "title": title.strip(" #*"), "summary": body.strip()}
            with SUMMARIES_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(cache[key], ensure_ascii=False) + "\n")
        com["title"], com["summary"] = cache[key]["title"], cache[key]["summary"]

    await asyncio.gather(*(one(c) for c in communities))


async def main_async(args) -> None:
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    chunks = [c for c in store.all_chunks() if c.metadata["section"] != "Recitals"]
    chunks.sort(key=lambda c: c.id)
    if args.limit:
        chunks = chunks[: args.limit]
    print(f"{len(chunks)} chunks (recitals excluded)")
    llm = make_llm(settings)
    EXTRACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    extractions = await Extractor(llm, EXTRACTIONS_PATH, args.concurrency).run(chunks)
    failed = sum(1 for e in extractions.values() if e.get("failed"))
    print(f"extraction done ({failed} failed)")

    g = build_structure(chunks, extractions)
    communities = detect_communities(g)
    if not args.no_summaries:
        await summarise(llm, g, communities, args.concurrency)
    graph = NetworkxGraphStore(g, communities)
    graph.save(GRAPH_PATH)
    print(json.dumps(graph.stats(), indent=2), f"\nsaved {GRAPH_PATH}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-summaries", action="store_true")
    p.add_argument("--concurrency", type=int, default=12)
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
