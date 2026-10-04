import asyncio
import json

from app.providers.base import Chunk
from app.providers.openai_compat import Usage, usage_var
from app.rag.agent import Agent, Budget


def chunk(source, section, text):
    meta = {"source": source, "section": section, "title": "t", "chapter": "", "part": 0}
    return Chunk(f"{source}:{section}:0", source, f"[{source.upper()} | {section}]\n{text}", meta)


CHUNKS = [
    chunk("gdpr", "Article 33", "Notify the authority within 72 hours."),
    chunk("nis2", "Article 23", "Early warning within 24 hours."),
]


class FakeRetriever:
    chunks = CHUNKS

    def retrieve(self, query, k):
        from app.providers.base import RetrievedChunk

        pick = [c for c in CHUNKS if c.metadata["source"] in query.lower()] or CHUNKS
        return [RetrievedChunk(c, 1.0) for c in pick][:k]


def tool_msg(*calls):
    return {
        "content": None,
        "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)
        ],
    }


class ScriptedLLM:
    """Returns the scripted assistant messages in order; records what it was asked."""

    def __init__(self, script, tokens_per_call=100):
        self.script, self.calls, self.tokens = list(script), [], tokens_per_call

    async def chat(self, messages, tools=None):
        self.calls.append((len(messages), tools is not None))
        if meter := usage_var.get():
            meter.add({"prompt_tokens": self.tokens, "completion_tokens": 0})
        return self.script.pop(0) if self.script else {"content": "final [1]", "tool_calls": None}


def run(agent, q="q"):
    return asyncio.run(agent.run(q))


def test_search_then_answer_numbers_evidence_and_traces():
    llm = ScriptedLLM(
        [
            tool_msg(("search", {"query": "gdpr breach"}), ("search", {"query": "nis2 incident"})),
            {"content": "GDPR 72h [1], NIS2 24h [2].", "tool_calls": None},
        ]
    )
    res = run(Agent(llm, FakeRetriever()))
    assert res.text.startswith("GDPR 72h")
    assert [s.metadata["section"] for s in [r.chunk for r in res.sources]] == [
        "Article 33",
        "Article 23",
    ]
    assert [t.tool for t in res.trace] == ["search", "search"]
    assert res.stop_reason == "answered" and res.tokens == 200


def test_loop_guard_forces_answer_without_tools():
    same = tool_msg(("search", {"query": "gdpr"}))
    llm = ScriptedLLM([same, same, same, same])
    res = run(Agent(llm, FakeRetriever(), budget=Budget(max_repeats=2, max_steps=8)))
    assert res.stop_reason == "loop_guard"
    assert llm.calls[-1][1] is False  # last call offered no tools
    assert any(t.note == "repeat" for t in res.trace)


def test_tool_budget_stops_searching():
    calls = [tool_msg(("search", {"query": f"gdpr {i}"})) for i in range(10)]
    llm = ScriptedLLM(calls)
    res = run(Agent(llm, FakeRetriever(), budget=Budget(max_tool_calls=2, max_steps=10)))
    assert res.stop_reason == "tool_budget"
    assert sum(1 for t in res.trace if t.note == "") == 2


def test_token_budget_stops():
    llm = ScriptedLLM(
        [tool_msg(("search", {"query": "gdpr a"})), tool_msg(("search", {"query": "gdpr b"}))],
        tokens_per_call=1000,
    )
    res = run(Agent(llm, FakeRetriever(), budget=Budget(max_tokens=1500, max_steps=8)))
    assert res.stop_reason == "token_budget"


def test_get_section_and_bad_arguments_do_not_crash():
    llm = ScriptedLLM(
        [
            tool_msg(
                ("get_section", {"source": "gdpr", "section": "Article 33"}),
                ("get_section", {"source": "gdpr", "section": "Article 999"}),
                ("search", {"nonsense": 1}),
                ("no_such_tool", {}),
            ),
            {"content": "done [1]", "tool_calls": None},
        ]
    )
    res = run(Agent(llm, FakeRetriever()))
    assert len(res.sources) == 1 and res.text == "done [1]"
    assert [t.new_passages for t in res.trace] == [1, 0, 0, 0]


def test_meter_is_shared_with_caller():
    meter = Usage()
    token = usage_var.set(meter)
    try:
        run(Agent(ScriptedLLM([{"content": "x", "tool_calls": None}]), FakeRetriever()))
    finally:
        usage_var.reset(token)
    assert meter.calls == 1
