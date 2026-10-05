import json
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.providers.base import Chunk, RetrievedChunk
from app.rag.cache import CachedAnswer, MemoEmbedder, SemanticCache, anchors, scope_key
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever
from app.rag.router import PipelineRunner, Router
from app.security.access import AccessPolicy
from app.security.auth import TokenVerifier
from app.security.mint import generate_keypair, mint_token

DIM = 64


def bow(text: str) -> list[float]:
    """Bag-of-words hashing embedder: shared words mean similar vectors, deterministic."""
    v = np.zeros(DIM)
    for word in text.lower().replace("?", "").split():
        v[zlib.crc32(word.encode()) % DIM] += 1
    return v.tolist()


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def make_cache(**kw):
    kw.setdefault("threshold", 0.8)
    return SemanticCache(bow, **kw)


ANSWER = CachedAnswer("baseline", None, "Within 72 hours [1].")


def test_paraphrase_hits_and_unrelated_question_misses():
    cache = make_cache()
    cache.store("s", "when must a personal data breach be reported to the authority", ANSWER)
    hit, sim = cache.lookup("s", "when must a personal data breach be reported to authority")
    assert hit is ANSWER and sim > 0.8
    assert cache.lookup("s", "what are the fines for ai act violations")[0] is None


def test_different_article_numbers_never_share_an_answer():
    cache = make_cache(threshold=0.5)
    cache.store("s", "what does article 5 of the gdpr require", ANSWER)
    # near-identical wording and a high similarity, but a different article
    hit, _ = cache.lookup("s", "what does article 6 of the gdpr require")
    assert hit is None
    assert cache.lookup("s", "what does article 5 of the gdpr require")[0] is ANSWER


def test_regulation_names_are_anchors():
    assert anchors("Does NIS2 apply?") != anchors("Does DORA apply?")
    assert anchors("GDPR article 33") == anchors("gdpr Article 33?")


def test_scope_isolates_what_users_may_see():
    assert scope_key(frozenset({"gdpr"}), "agent", 7) != scope_key(
        frozenset({"gdpr", "dora"}), "agent", 7
    )
    assert scope_key(None, "agent", 7) != scope_key(frozenset(), "agent", 7)
    assert scope_key(frozenset({"a", "b"}), "agent", 7) == scope_key(
        frozenset({"b", "a"}), "agent", 7
    )
    assert scope_key(None, "agent", 7) != scope_key(None, "baseline", 7)
    assert scope_key(None, "agent", 7) != scope_key(None, "agent", 8)
    cache = make_cache()
    cache.store(scope_key(frozenset({"gdpr", "dora"}), "agent", 7), "breach deadline", ANSWER)
    assert cache.lookup(scope_key(frozenset({"gdpr"}), "agent", 7), "breach deadline")[0] is None


def test_entries_expire():
    clock = Clock()
    cache = make_cache(ttl_seconds=60, clock=clock)
    cache.store("s", "breach deadline", ANSWER)
    clock.now = 59
    assert cache.lookup("s", "breach deadline")[0] is ANSWER
    clock.now = 61
    assert cache.lookup("s", "breach deadline")[0] is None
    assert len(cache) == 0


def test_cache_is_bounded_and_evicts_the_least_recently_used():
    cache = make_cache(max_entries=2)
    cache.store("s", "alpha question", CachedAnswer("baseline", None, "a"))
    cache.store("s", "bravo query", CachedAnswer("baseline", None, "b"))
    assert cache.lookup("s", "alpha question")[0].text == "a"  # refreshes alpha
    cache.store("s", "charlie request", CachedAnswer("baseline", None, "c"))
    assert len(cache) == 2
    assert cache.lookup("s", "bravo query")[0] is None
    assert cache.lookup("s", "alpha question")[0].text == "a"


def test_memo_embedder_embeds_a_repeated_query_once():
    calls = []

    class Inner:
        def embed(self, texts):
            calls.append(texts)
            return [[1.0, 2.0] for _ in texts]

    memo = MemoEmbedder(Inner(), size=2)
    assert memo.embed(["q"]) == memo.embed(["q"]) == [[1.0, 2.0]]
    assert len(calls) == 1
    memo.embed(["a", "b"])  # batches are not memoised
    memo.embed(["a", "b"])
    assert len(calls) == 3


# ---- through the API ---------------------------------------------------------------------------

ISSUER, AUDIENCE = "https://azrag.test", "azrag-api"


def chunk(source):
    meta = {"source": source, "section": "Article 1", "title": "t", "chapter": "", "part": 0}
    return Chunk(f"{source}:1", source, f"incident reporting {source}", meta)


CHUNKS = [chunk("gdpr"), chunk("dora")]


class Store:
    def all_chunks(self):
        return CHUNKS

    def search(self, emb, k, filters=None):
        allowed = (filters or {}).get("source", {}).get("$in")
        return [
            RetrievedChunk(c, 1.0)
            for c in CHUNKS
            if allowed is None or c.metadata["source"] in allowed
        ][:k]


class CountingLLM:
    def __init__(self, text="answer [1]"):
        self.text, self.streams = text, 0

    async def generate(self, system, user):
        return "lookup"

    async def stream(self, system, user):
        self.streams += 1
        yield self.text


class Embedder:
    def embed(self, texts):
        return [bow(t) for t in texts]


@pytest.fixture(scope="module")
def keypair():
    private, public = generate_keypair()
    return private.decode(), public.decode()


@pytest.fixture
def setup(monkeypatch, keypair):
    def make(llm=None):
        llm = llm or CountingLLM()
        retriever = Retriever(Embedder(), Store(), RetrievalConfig(hybrid=True))
        baseline = RAGPipeline(retriever, llm)
        services = main.Services(
            baseline,
            Router(llm, {"baseline": PipelineRunner(baseline, 7)}),
            auth=TokenVerifier(ISSUER, AUDIENCE, keypair[1]),
            policy=AccessPolicy.from_json('{"dora": ["finance"]}'),
            all_sources=frozenset({"gdpr", "dora"}),
            audit_salt="t",
            cache=make_cache(),
        )
        monkeypatch.setattr(main, "build_services", lambda: services)
        return TestClient(main.app), llm

    return make


def ask(client, keypair, question, groups=(), sub="u"):
    token = mint_token(keypair[0], ISSUER, AUDIENCE, sub, list(groups), 3600)
    resp = client.post(
        "/chat",
        json={"question": question, "mode": "baseline"},
        headers={"Authorization": f"Bearer {token}"},
    )
    events = {}
    for block in resp.text.strip().split("\n\n"):
        name, data = block.split("\n")
        events.setdefault(name.removeprefix("event: "), []).append(
            json.loads(data.removeprefix("data: "))
        )
    return events


def test_second_identical_question_is_served_from_cache(setup, keypair):
    client, llm = setup()
    with client as c:
        first = ask(c, keypair, "when is incident reporting due")
        second = ask(c, keypair, "when is incident reporting due")
    assert first["done"][0]["cached"] is False and llm.streams == 1
    assert second["done"][0]["cached"] is True and llm.streams == 1  # no second LLM call
    assert second["route"][0]["cached"] is True
    assert "".join(second["token"]) == "answer [1]"
    assert second["sources"][0][0]["source"] == "gdpr"
    assert second["done"][0]["tokens"] == 0


def test_cache_never_crosses_access_scopes(setup, keypair):
    client, llm = setup()
    with client as c:
        finance = ask(c, keypair, "incident reporting duties", groups=["finance"])
        other = ask(c, keypair, "incident reporting duties", groups=[], sub="v")
    assert {s["source"] for s in finance["sources"][0]} == {"gdpr", "dora"}
    assert other["done"][0]["cached"] is False  # not served from the finance user's entry
    assert {s["source"] for s in other["sources"][0]} == {"gdpr"}
    assert llm.streams == 2


def test_content_filter_replies_are_not_cached(setup, keypair):
    client, llm = setup(CountingLLM("Request blocked by the content safety filter."))
    with client as c:
        ask(c, keypair, "incident reporting duties")
        again = ask(c, keypair, "incident reporting duties")
    assert again["done"][0]["cached"] is False and llm.streams == 2
