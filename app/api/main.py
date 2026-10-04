"""FastAPI app. POST /chat streams Server-Sent Events:

route    which pipeline answers (auto mode: after a one-call classification)
step     agent progress: each tool call as it finishes
sources  the passages the answer is built from, numbered as the answer cites them
token    answer text (streamed for baseline/graph; one event for agent/global)
done     timings and token usage        error  something failed
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.graph.loader import load_graph
from app.graph.search import GlobalSearch, GraphConfig, GraphRetriever
from app.providers.factory import make_embedder, make_llm, make_store
from app.providers.openai_compat import Usage, usage_var
from app.rag.agent import Agent
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever
from app.rag.router import PipelineRunner, Router

log = logging.getLogger("api")
Mode = Literal["auto", "baseline", "graph", "agent", "global"]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=7, ge=1, le=20)
    mode: Mode = "auto"


@dataclass
class Services:
    baseline: RAGPipeline
    router: Router
    graph: RAGPipeline | None = None
    agent: Agent | None = None
    global_search: GlobalSearch | None = None

    def available(self) -> list[str]:
        have = {
            "baseline": self.baseline,
            "graph": self.graph,
            "agent": self.agent,
            "global": self.global_search,
        }
        return [name for name, svc in have.items() if svc is not None]


def build_services() -> Services:
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    config = RetrievalConfig(
        exclude_recitals=True, hybrid=settings.retrieval_hybrid, rerank=settings.retrieval_rerank
    )
    retriever = Retriever(embedder, store, config)
    llm = make_llm(settings)
    baseline = RAGPipeline(retriever, llm)
    graph = load_graph(settings)
    if graph is None or llm is None:
        log.warning("running without graph features (graph=%s, llm=%s)", graph, llm)
        return Services(baseline, Router(llm, {"baseline": PipelineRunner(baseline, 7)}))
    graph_pipeline = RAGPipeline(GraphRetriever(retriever, graph, GraphConfig(n_graph=2)), llm)
    agent = Agent(llm, retriever, graph)
    global_search = GlobalSearch(llm, graph, embedder=embedder) if graph.communities() else None
    runners = {"baseline": PipelineRunner(baseline, 7), "agent": agent}
    return Services(baseline, Router(llm, runners), graph_pipeline, agent, global_search)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = build_services()  # loads the BM25 index and graph once, at startup
    # Warm the credential cache and TLS connections so the first user request is not the slow one.
    await asyncio.to_thread(app.state.services.baseline.retriever.retrieve, "warm-up", 1)
    yield


app = FastAPI(title="EU Regulatory Compliance Assistant", lifespan=lifespan)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def source_dict(rank: int, rc) -> dict:
    m = rc.chunk.metadata
    return {
        "rank": rank,
        "source": m.get("source", ""),
        "section": m.get("section", ""),
        "title": m.get("title", ""),
        "text": rc.chunk.text,
        "score": rc.score,
    }


def sources_event(sources) -> str:
    return sse("sources", [source_dict(i, rc) for i, rc in enumerate(sources, 1)])


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/pipelines")
async def pipelines(request: Request) -> dict:
    return {"available": request.app.state.services.available()}


async def choose(svc: Services, body: ChatRequest) -> tuple[str, str | None]:
    """(pipeline, question kind). Unavailable pipelines fall back to the baseline."""
    if body.mode != "auto":
        return (body.mode if body.mode in svc.available() else "baseline"), None
    kind, _ = await svc.router.classify(body.question)
    name = svc.router.table.get(kind, svc.router.default)
    return (name if name in svc.available() else "baseline"), kind


@app.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    svc: Services = request.app.state.services

    async def events():
        start, first_token = time.perf_counter(), None
        meter = Usage()
        usage_var.set(meter)  # everything this request spends on the LLM is counted here
        try:
            name, kind = await choose(svc, body)
            yield sse("route", {"pipeline": name, "kind": kind})

            if name in ("baseline", "graph"):
                pipeline = svc.graph if name == "graph" else svc.baseline
                async for what, value in pipeline.stream(body.question, body.k):
                    if what == "sources":
                        yield sources_event(value)
                    else:
                        first_token = first_token or time.perf_counter() - start
                        yield sse("token", value)
            else:
                steps: asyncio.Queue = asyncio.Queue()
                runner = svc.agent if name == "agent" else svc.global_search
                work = (
                    asyncio.create_task(runner.run(body.question, on_step=steps.put))
                    if name == "agent"
                    else asyncio.create_task(runner.run(body.question))
                )
                while not work.done() or not steps.empty():
                    try:
                        step = await asyncio.wait_for(steps.get(), timeout=0.2)
                    except TimeoutError:
                        continue
                    yield sse(
                        "step",
                        {
                            "tool": step.tool,
                            "args": step.args,
                            "n": step.n,
                            "new_passages": step.new_passages,
                        },
                    )
                answer = await work
                yield sources_event(answer.sources)
                first_token = time.perf_counter() - start
                yield sse("token", answer.text)

            done = {
                "ttft_s": first_token,
                "total_s": time.perf_counter() - start,
                "tokens": meter.prompt_tokens + meter.completion_tokens,
                "llm_calls": meter.calls,
            }
            yield sse("done", done)
        except Exception:
            log.exception("chat failed")
            yield sse("error", {"message": "The assistant failed to answer. Try again."})

    # no-cache + X-Accel-Buffering stop proxies from buffering the stream into one blob
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
