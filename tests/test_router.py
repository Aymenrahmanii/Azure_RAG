import asyncio

from app.providers.base import Chunk, RetrievedChunk
from app.providers.openai_compat import ContentFiltered
from app.rag.pipeline import Answer
from app.rag.router import Router


class LabelLLM:
    def __init__(self, label=None, exc=None):
        self.label, self.exc = label, exc

    async def generate(self, system, user):
        if self.exc:
            raise self.exc
        return self.label


class Runner:
    def __init__(self, name):
        self.name = name

    async def run(self, question):
        src = [RetrievedChunk(Chunk("c", "d", "t"), 1.0)]
        return Answer(f"{self.name}: {question}", src)


def router(llm):
    return Router(llm, {n: Runner(n) for n in ("baseline", "agent", "global")})


def ask(r, q="q"):
    return asyncio.run(r.run(q))


def test_routes_by_kind():
    assert ask(router(LabelLLM("lookup"))).route == "baseline"
    assert ask(router(LabelLLM("multi"))).route == "agent"
    assert ask(router(LabelLLM("broad"))).route == "agent"  # global-only search lost the eval


def test_label_parsing_is_forgiving():
    assert ask(router(LabelLLM(' "Broad".\n'))).route == "agent"


def test_garbage_label_falls_back_to_baseline_and_is_flagged():
    out = ask(router(LabelLLM("I think it is complicated")))
    assert out.route == "baseline" and out.classifier_failed


def test_classifier_error_falls_back():
    out = ask(router(LabelLLM(exc=RuntimeError("boom"))))
    assert out.route == "baseline" and out.classifier_failed


def test_content_filter_routes_to_baseline_without_flag():
    out = ask(router(LabelLLM(exc=ContentFiltered("blocked"))))
    assert out.route == "baseline" and not out.classifier_failed


def test_missing_pipeline_uses_default():
    r = Router(LabelLLM("multi"), {"baseline": Runner("baseline")})
    assert ask(r).route == "baseline"
