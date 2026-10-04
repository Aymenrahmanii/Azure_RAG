import asyncio
import json
import logging
import time

import jwt
import networkx as nx
import pytest

from app.graph.search import GlobalSearch, GraphConfig, GraphRetriever
from app.graph.store import NetworkxGraphStore
from app.providers.azure_search import odata_filter
from app.providers.base import Chunk
from app.rag.agent import Agent
from app.rag.retrieval import RetrievalConfig, Retriever
from app.security import audit
from app.security.access import AccessPolicy, is_visible, reset_visible, set_visible
from app.security.auth import TokenError, TokenVerifier
from app.security.mint import generate_keypair, mint_token
from app.security.ratelimit import SlidingWindowLimiter

ISSUER, AUDIENCE = "https://azrag.test", "azrag-api"


@pytest.fixture(scope="module")
def keys():
    private, public = generate_keypair()
    return private.decode(), public.decode()


def token(private, **overrides):
    args = {"issuer": ISSUER, "audience": AUDIENCE, "sub": "alice", "groups": ["finance"]}
    args.update({"ttl_seconds": 3600, **overrides})
    return mint_token(private, **args)


# ---- token verification --------------------------------------------------------------------


def test_valid_token_yields_user_with_groups(keys):
    private, public = keys
    user = TokenVerifier(ISSUER, AUDIENCE, public).verify(token(private, name="Alice"))
    assert user.id == "alice" and user.groups == {"finance"} and user.name == "Alice"


@pytest.mark.parametrize(
    "overrides",
    [
        {"audience": "someone-elses-api"},
        {"issuer": "https://evil.test"},
        {"ttl_seconds": -3600},  # expired (beyond the 30 s leeway)
    ],
)
def test_wrong_audience_issuer_or_expiry_is_rejected(keys, overrides):
    private, public = keys
    with pytest.raises(TokenError):
        TokenVerifier(ISSUER, AUDIENCE, public).verify(token(private, **overrides))


def test_token_signed_by_another_key_is_rejected(keys):
    other_private, _ = generate_keypair()
    with pytest.raises(TokenError):
        TokenVerifier(ISSUER, AUDIENCE, keys[1]).verify(token(other_private.decode()))


def hs256_with_secret(claims: dict, secret: bytes) -> str:
    """Built by hand: PyJWT refuses to sign HS256 with a PEM key, an attacker would not."""
    import base64
    import hashlib
    import hmac

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = (
        b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + b64(json.dumps(claims).encode())
    )
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64(sig)).decode()


def test_alg_none_and_hs256_key_confusion_are_rejected(keys):
    _, public = keys
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "mallory",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "groups": ["finance"],
    }
    verifier = TokenVerifier(ISSUER, AUDIENCE, public)
    forged_none = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(TokenError):
        verifier.verify(forged_none)
    # the classic attack: sign with HS256 using the (public) RSA key as the HMAC secret
    forged_hs = hs256_with_secret(claims, public.encode())
    with pytest.raises(TokenError):
        verifier.verify(forged_hs)


def test_missing_required_claim_and_garbage_are_rejected(keys):
    private, public = keys
    verifier = TokenVerifier(ISSUER, AUDIENCE, public)
    no_sub = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "iat": 1, "exp": int(time.time()) + 60},
        private,
        algorithm="RS256",
    )
    for bad in (no_sub, "not-a-jwt", ""):
        with pytest.raises(TokenError):
            verifier.verify(bad)


def test_roles_claim_is_accepted_as_groups_like_entra_app_roles(keys):
    private, public = keys
    now = int(time.time())
    t = jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "bob",
            "iat": now,
            "exp": now + 60,
            "roles": ["Finance"],
        },
        private,
        algorithm="RS256",
    )
    assert TokenVerifier(ISSUER, AUDIENCE, public).verify(t).groups == {"Finance"}


def test_verifier_configuration_is_validated(keys):
    with pytest.raises(ValueError):
        TokenVerifier(ISSUER, AUDIENCE)  # no key source
    with pytest.raises(ValueError):
        TokenVerifier(ISSUER, AUDIENCE, public_key=keys[1], jwks_url="https://x/keys")
    with pytest.raises(ValueError):
        TokenVerifier("", AUDIENCE, keys[1])  # an unchecked issuer/audience accepts anything


# ---- access policy -------------------------------------------------------------------------


def test_policy_visible_sources():
    policy = AccessPolicy.from_json('{"dora": ["finance"], "nis2": ["ops", "finance"]}')
    everything = {"gdpr", "dora", "nis2"}
    assert policy.visible_sources([], everything) == {"gdpr"}
    assert policy.visible_sources(["finance"], everything) == {"gdpr", "dora", "nis2"}
    assert policy.visible_sources(["ops"], everything) == {"gdpr", "nis2"}
    assert AccessPolicy.from_json("").visible_sources([], everything) == everything


def test_visibility_context_defaults_to_unrestricted_and_resets():
    assert is_visible("dora")
    t = set_visible({"gdpr"})
    assert is_visible("gdpr") and not is_visible("dora")
    reset_visible(t)
    assert is_visible("dora")


# ---- trimming in every retrieval path ------------------------------------------------------


def chunk(source, section, text="data breach notification"):
    meta = {"source": source, "section": section, "title": "t", "chapter": "", "part": 0}
    return Chunk(f"{source}:{section}:0", source, f"{text} {source} {section}", meta)


CHUNKS = [chunk("gdpr", "Article 33"), chunk("dora", "Article 19"), chunk("nis2", "Article 23")]


class FakeEmbedder:
    def embed(self, texts):
        return [[1.0] for _ in texts]


class FakeStore:
    """Honours the `source` $in filter the way the real stores do."""

    def __init__(self):
        self.filters = []

    def all_chunks(self):
        return CHUNKS

    def search(self, emb, k, filters=None):
        from app.providers.base import RetrievedChunk

        self.filters.append(filters)
        allowed = (filters or {}).get("source", {}).get("$in")
        pool = [c for c in CHUNKS if allowed is None or c.metadata["source"] in allowed]
        return [RetrievedChunk(c, 1.0) for c in pool][:k]


def sources_of(results):
    return {r.chunk.metadata["source"] for r in results}


def test_retriever_never_returns_restricted_sources_dense_or_bm25():
    store = FakeStore()
    retriever = Retriever(FakeEmbedder(), store, RetrievalConfig(hybrid=True))
    t = set_visible({"gdpr", "nis2"})
    try:
        results = retriever.retrieve("data breach notification dora", 10)
        # the BM25 side too: the query names dora, which would win if it were not masked
        assert sources_of(results) == {"gdpr", "nis2"}
        assert store.filters[-1]["source"] == {"$in": ["gdpr", "nis2"]}
    finally:
        reset_visible(t)
    assert "dora" in sources_of(retriever.retrieve("data breach notification dora", 10))


def test_empty_visibility_returns_nothing_not_everything():
    retriever = Retriever(FakeEmbedder(), FakeStore(), RetrievalConfig(hybrid=True))
    t = set_visible(set())
    try:
        assert retriever.retrieve("data breach", 10) == []
    finally:
        reset_visible(t)


def test_odata_filter_in_and_empty_set():
    assert (
        odata_filter({"source": {"$in": ["gdpr", "nis2"]}}) == "search.in(source, 'gdpr,nis2', ',')"
    )
    assert odata_filter({"source": {"$in": []}}) == "false"  # an empty allow-list matches nothing
    with pytest.raises(ValueError):
        odata_filter({"source": {"$in": ["a,b"]}})
    both = odata_filter({"section": {"$ne": "Recitals"}, "source": {"$in": ["gdpr"]}})
    assert both == "section ne 'Recitals' and search.in(source, 'gdpr', ',')"


def make_graph():
    g = nx.MultiDiGraph()
    for s in ("gdpr:Article 33", "dora:Article 19", "nis2:Article 23"):
        g.add_node(s, kind="section")
    g.add_edge("gdpr:Article 33", "dora:Article 19", kind="references")
    g.add_edge("gdpr:Article 33", "nis2:Article 23", kind="references")
    g.add_node("e:breach", kind="entity", name="breach", type="concept", description="")
    for s in ("gdpr:Article 33", "dora:Article 19"):
        g.add_edge(s, "e:breach", kind="mentions")
    comms = [
        {
            "id": 0,
            "title": "Breach",
            "summary": "breach rules",
            "entities": ["e:breach"],
            "sections": ["gdpr:Article 33"],
        },
    ]
    return NetworkxGraphStore(g, comms)


def test_graph_expansion_skips_restricted_sections():
    base = Retriever(FakeEmbedder(), FakeStore(), RetrievalConfig(hybrid=True))
    gr = GraphRetriever(base, make_graph(), GraphConfig(n_graph=3, seeds=1, min_idf=0.0))
    t = set_visible({"gdpr", "nis2"})
    try:
        out = gr.retrieve("breach", 4)
    finally:
        reset_visible(t)
    assert "dora" not in sources_of(out)
    assert "nis2" in sources_of(out) or "gdpr" in sources_of(out)


class NoLLM:
    pass


def test_agent_tools_do_not_reveal_restricted_sections():
    base = Retriever(FakeEmbedder(), FakeStore(), RetrievalConfig(hybrid=True))
    agent = Agent(NoLLM(), base, make_graph())
    from app.rag.agent import Evidence

    ev = Evidence()
    t = set_visible({"gdpr", "nis2"})
    try:
        text, new = asyncio.run(agent._get_section(ev, "dora", "Article 19"))
        related, _ = asyncio.run(agent._related("gdpr", "Article 33"))
        hidden_related, _ = asyncio.run(agent._related("dora", "Article 19"))
    finally:
        reset_visible(t)
    assert new == 0 and not ev.items and text.startswith("No section")  # same as a missing one
    assert "dora" not in related and "nis2:Article 23" in related
    assert hidden_related.startswith("No section")
    t = set_visible({"dora"})
    try:
        assert asyncio.run(agent._get_section(Evidence(), "dora", "Article 19"))[1] == 1
    finally:
        reset_visible(t)


def test_global_search_hides_communities_that_draw_on_restricted_sources():
    gs = GlobalSearch(NoLLM(), make_graph())  # the community's entity appears in gdpr AND dora
    assert len(gs._passages("breach")) == 1
    t = set_visible({"gdpr", "nis2"})
    try:
        assert gs._passages("breach") == []
    finally:
        reset_visible(t)


# ---- rate limit and audit ------------------------------------------------------------------


def test_sliding_window_limiter():
    now = [0.0]
    lim = SlidingWindowLimiter(2, 60, clock=lambda: now[0])
    assert lim.check("a")[0] and lim.check("a")[0]
    allowed, retry = lim.check("a")
    assert not allowed and 0 < retry <= 60
    assert lim.check("b")[0]  # other callers are unaffected
    now[0] = 61
    assert lim.check("a")[0]
    assert SlidingWindowLimiter(0).check("x") == (True, 0.0)  # 0 disables


def test_audit_record_has_no_question_text_or_raw_user_id(caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        entry = audit.record(
            user_id="alice@corp.example",
            question="my iban is DE89 3704 0044 0532 0130 00",
            status="ok",
            pipeline="agent",
            sections=["gdpr:Article 33"],
            salt="s",
        )
    line = caplog.records[-1].getMessage()
    assert "iban" not in line.lower() and "alice" not in line and "DE89" not in line
    assert json.loads(line) == entry and entry["question_len"] > 0 and len(entry["user"]) == 12
    assert audit.short_hash("x", "s1") != audit.short_hash("x", "s2")
