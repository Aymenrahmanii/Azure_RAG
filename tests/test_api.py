import json

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.providers.base import Chunk, RetrievedChunk
from app.rag.pipeline import RAGPipeline


class FakeRetriever:
    def retrieve(self, query, k):
        meta = {"source": "GDPR", "section": "Article 5", "title": "Principles"}
        return [RetrievedChunk(Chunk("c1", "gdpr", "Data must be accurate.", meta), 0.9)]


class FakeLLM:
    async def generate(self, system, user):
        return "Accurate [1]."

    async def stream(self, system, user):
        for piece in ("Accurate ", "[1]."):
            yield piece


class BrokenLLM(FakeLLM):
    async def stream(self, system, user):
        raise RuntimeError("boom")
        yield


def parse(body: str) -> list[tuple[str, object]]:
    events = []
    for block in body.strip().split("\n\n"):
        name, data = block.split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def client(monkeypatch):
    def make(llm):
        monkeypatch.setattr(main, "build_pipeline", lambda: RAGPipeline(FakeRetriever(), llm))
        return TestClient(main.app)

    return make


def test_chat_streams_sources_tokens_done(client):
    with client(FakeLLM()) as c:
        resp = c.post("/chat", json={"question": "What about accuracy?"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse(resp.text)
    assert [e[0] for e in events] == ["sources", "token", "token", "done"]
    assert events[0][1][0]["section"] == "Article 5"
    assert "".join(e[1] for e in events if e[0] == "token") == "Accurate [1]."


def test_chat_reports_failure_as_event(client):
    with client(BrokenLLM()) as c:
        events = parse(c.post("/chat", json={"question": "x"}).text)
    assert events[-1][0] == "error"


def test_chat_validates_input(client):
    with client(FakeLLM()) as c:
        assert c.post("/chat", json={"question": ""}).status_code == 422
        assert c.post("/chat", json={"question": "x", "k": 99}).status_code == 422


def test_healthz(client):
    with client(FakeLLM()) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
