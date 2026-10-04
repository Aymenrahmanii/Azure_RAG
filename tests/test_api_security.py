import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.core.config import Settings
from app.providers.base import Chunk, RetrievedChunk
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever
from app.rag.router import PipelineRunner, Router
from app.security.access import AccessPolicy
from app.security.auth import TokenVerifier
from app.security.mint import generate_keypair, mint_token
from app.security.ratelimit import SlidingWindowLimiter

ISSUER, AUDIENCE = "https://azrag.test", "azrag-api"
SECRET_QUESTION = "my iban is DE89370400440532013000, what must I report"


def chunk(source, section):
    meta = {"source": source, "section": section, "title": "t", "chapter": "", "part": 0}
    return Chunk(f"{source}:{section}:0", source, f"incident reporting {source}", meta)


CHUNKS = [chunk("gdpr", "Article 33"), chunk("dora", "Article 19")]


class Embedder:
    def embed(self, texts):
        return [[1.0] for _ in texts]


class Store:
    def all_chunks(self):
        return CHUNKS

    def search(self, emb, k, filters=None):
        allowed = (filters or {}).get("source", {}).get("$in")
        pool = [c for c in CHUNKS if allowed is None or c.metadata["source"] in allowed]
        return [RetrievedChunk(c, 1.0) for c in pool][:k]


class LLM:
    async def generate(self, system, user):
        return "lookup"

    async def stream(self, system, user):
        yield "answer [1]"


@pytest.fixture(scope="module")
def keypair():
    private, public = generate_keypair()
    return private.decode(), public.decode()


@pytest.fixture
def make_client(monkeypatch, keypair):
    def make(limit=100):
        retriever = Retriever(Embedder(), Store(), RetrievalConfig(hybrid=True))
        baseline = RAGPipeline(retriever, LLM())
        services = main.Services(
            baseline,
            Router(LLM(), {"baseline": PipelineRunner(baseline, 7)}),
            auth=TokenVerifier(ISSUER, AUDIENCE, keypair[1]),
            policy=AccessPolicy.from_json('{"dora": ["finance"]}'),
            limiter=SlidingWindowLimiter(limit),
            all_sources=frozenset({"gdpr", "dora"}),
            audit_salt="test",
        )
        monkeypatch.setattr(main, "build_services", lambda: services)
        return TestClient(main.app)

    return make


def bearer(keypair, sub="alice", groups=(), **kw):
    t = mint_token(keypair[0], ISSUER, AUDIENCE, sub, list(groups), 3600, **kw)
    return {"Authorization": f"Bearer {t}"}


def chat(client, headers=None, question="incident reporting?"):
    return client.post(
        "/chat", json={"question": question, "mode": "baseline"}, headers=headers or {}
    )


def sources(resp):
    for block in resp.text.strip().split("\n\n"):
        name, data = block.split("\n")
        if name == "event: sources":
            return [(s["source"], s["section"]) for s in json.loads(data.removeprefix("data: "))]
    return None


def test_requests_without_a_valid_token_are_rejected(make_client, keypair):
    with make_client() as c:
        no_token = chat(c)
        garbage = chat(c, {"Authorization": "Bearer nope"})
        wrong_scheme = chat(c, {"Authorization": "Basic abc"})
        pipelines = c.get("/pipelines")
    assert [r.status_code for r in (no_token, garbage, wrong_scheme, pipelines)] == [401] * 4
    assert no_token.headers["www-authenticate"] == "Bearer"
    assert "signature" not in garbage.text.lower()  # the reason is logged, not returned


def test_expired_token_is_rejected(make_client, keypair):
    t = mint_token(keypair[0], ISSUER, AUDIENCE, "alice", [], -3600)
    with make_client() as c:
        assert chat(c, {"Authorization": f"Bearer {t}"}).status_code == 401


def test_health_check_and_security_headers_need_no_token(make_client):
    with make_client() as c:
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_group_membership_controls_which_documents_are_returned(make_client, keypair):
    with make_client() as c:
        outsider = sources(chat(c, bearer(keypair)))
        finance = sources(chat(c, bearer(keypair, "bob", ["finance"])))
    assert outsider == [("gdpr", "Article 33")]  # dora is trimmed before the LLM ever sees it
    assert set(finance) == {("gdpr", "Article 33"), ("dora", "Article 19")}


def test_visibility_does_not_leak_between_requests(make_client, keypair):
    with make_client() as c:
        first = sources(chat(c, bearer(keypair, "bob", ["finance"])))
        second = sources(chat(c, bearer(keypair, "alice")))
    assert len(first) == 2 and second == [("gdpr", "Article 33")]


def test_rate_limit_per_user(make_client, keypair):
    with make_client(limit=2) as c:
        alice = bearer(keypair, "alice")
        codes = [chat(c, alice).status_code for _ in range(3)]
        other = chat(c, bearer(keypair, "bob")).status_code
        limited = chat(c, alice)
    assert codes == [200, 200, 429] and other == 200
    assert int(limited.headers["retry-after"]) >= 1


def test_audit_log_records_who_and_what_but_not_the_question(make_client, keypair, caplog):
    with caplog.at_level(logging.INFO, logger="audit"):
        with make_client() as c:
            chat(c, bearer(keypair, "alice@corp.example"), SECRET_QUESTION)
    entry = json.loads(caplog.records[-1].getMessage())
    assert entry["status"] == "ok" and entry["pipeline"] == "baseline"
    assert entry["sections"] == ["gdpr:Article 33"]
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "DE89" not in log_text and "iban" not in log_text and "alice@corp" not in log_text


# ---- startup is fail-closed -----------------------------------------------------------------


def cfg(**kw):
    return Settings(_env_file=None, **kw)


def test_non_local_environment_refuses_to_start_without_authentication():
    with pytest.raises(RuntimeError, match="AUTH_MODE=jwt"):
        main.security_from_settings(cfg(environment="dev", auth_mode="off"))


def test_local_environment_may_run_without_authentication():
    auth, policy, _ = main.security_from_settings(cfg(environment="local"))
    assert auth is None and not policy.restricted


def test_acl_without_authentication_cannot_be_enforced():
    with pytest.raises(RuntimeError, match="cannot be enforced"):
        main.security_from_settings(cfg(environment="local", acl_restricted='{"dora": ["x"]}'))


def test_jwt_mode_requires_a_key_issuer_and_audience(keypair):
    with pytest.raises(ValueError):
        main.security_from_settings(cfg(environment="dev", auth_mode="jwt"))
    auth, _, _ = main.security_from_settings(
        cfg(
            environment="dev",
            auth_mode="jwt",
            auth_issuer=ISSUER,
            auth_audience=AUDIENCE,
            auth_public_key=keypair[1],
        )
    )
    assert auth is not None
