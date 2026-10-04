import json

import pytest
from fastapi.testclient import TestClient

from app.api import main
from app.providers.base import Chunk, RetrievedChunk
from app.rag.agent import Agent
from app.rag.pipeline import RAGPipeline
from app.rag.router import PipelineRunner, Router


def make_chunk(source="gdpr", section="Article 5"):
    meta = {"source": source, "section": section, "title": "Principles", "chapter": "", "part": 0}
    return Chunk(f"{source}:{section}:0", source, "Data must be accurate.", meta)


class FakeRetriever:
    chunks = [make_chunk()]

    def retrieve(self, query, k):
        return [RetrievedChunk(self.chunks[0], 0.9)]


class FakeLLM:
    """Handles every LLM entry point the API uses: generate (classifier), stream, chat (agent)."""

    def __init__(self, label="lookup", fail_stream=False):
        self.label, self.fail_stream, self.agent_turns = label, fail_stream, 0

    async def generate(self, system, user):
        return self.label

    async def stream(self, system, user):
        if self.fail_stream:
            raise RuntimeError("boom")
        for piece in ("Accurate ", "[1]."):
            yield piece

    async def chat(self, messages, tools=None):
        self.agent_turns += 1
        if self.agent_turns == 1:
            call = {"name": "search", "arguments": json.dumps({"query": "accuracy"})}
            return {
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": call}],
            }
        return {"content": "Agent says accurate [1].", "tool_calls": None}


def parse(body: str) -> list[tuple[str, object]]:
    events = []
    for block in body.strip().split("\n\n"):
        name, data = block.split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest.fixture
def client(monkeypatch):
    def make(llm=None, with_agent=True):
        llm = llm or FakeLLM()
        retriever = FakeRetriever()
        baseline = RAGPipeline(retriever, llm)
        runners = {"baseline": PipelineRunner(baseline, 7)}
        agent = Agent(llm, retriever) if with_agent else None
        if agent:
            runners["agent"] = agent
        services = main.Services(baseline, Router(llm, runners), agent=agent)
        monkeypatch.setattr(main, "build_services", lambda: services)
        return TestClient(main.app)

    return make


def names(events):
    return [e[0] for e in events]


def test_baseline_streams_route_sources_tokens_done(client):
    with client() as c:
        resp = c.post("/chat", json={"question": "What about accuracy?", "mode": "baseline"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse(resp.text)
    assert names(events) == ["route", "sources", "token", "token", "done"]
    assert events[0][1] == {"pipeline": "baseline", "kind": None}
    assert events[1][1][0]["section"] == "Article 5"
    assert "".join(e[1] for e in events if e[0] == "token") == "Accurate [1]."
    assert "total_s" in events[-1][1] and "tokens" in events[-1][1]


def test_auto_mode_routes_lookup_to_baseline(client):
    with client(FakeLLM("lookup")) as c:
        events = parse(c.post("/chat", json={"question": "deadline?", "mode": "auto"}).text)
    assert events[0] == ("route", {"pipeline": "baseline", "kind": "lookup"})


def test_auto_mode_routes_multi_to_agent_and_reports_steps(client):
    with client(FakeLLM("multi")) as c:
        events = parse(c.post("/chat", json={"question": "compare A and B", "mode": "auto"}).text)
    assert events[0] == ("route", {"pipeline": "agent", "kind": "multi"})
    assert names(events) == ["route", "step", "sources", "token", "done"]
    assert events[1][1]["tool"] == "search" and events[1][1]["new_passages"] == 1
    assert events[3][1] == "Agent says accurate [1]."


def test_unavailable_pipeline_falls_back_to_baseline(client):
    with client(with_agent=False) as c:
        events = parse(c.post("/chat", json={"question": "x", "mode": "agent"}).text)
        assert c.get("/pipelines").json() == {"available": ["baseline"]}
    assert events[0][1]["pipeline"] == "baseline"


def test_failure_is_reported_as_event(client):
    with client(FakeLLM(fail_stream=True)) as c:
        events = parse(c.post("/chat", json={"question": "x", "mode": "baseline"}).text)
    assert events[-1][0] == "error"


def test_validation_and_health(client):
    with client() as c:
        assert c.post("/chat", json={"question": ""}).status_code == 422
        assert c.post("/chat", json={"question": "x", "k": 99}).status_code == 422
        assert c.post("/chat", json={"question": "x", "mode": "nope"}).status_code == 422
        assert c.get("/healthz").json() == {"status": "ok"}


def test_default_mode_is_agent_with_baseline_fallback(client):
    with client() as c:
        first = parse(c.post("/chat", json={"question": "x"}).text)[0]
    assert first == ("route", {"pipeline": "agent", "kind": None})
    with client(with_agent=False) as c:
        first = parse(c.post("/chat", json={"question": "x"}).text)[0]
    assert first[1]["pipeline"] == "baseline"
