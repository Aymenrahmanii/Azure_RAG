"""FastAPI app. POST /chat streams Server-Sent Events: sources, then tokens, then done."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.providers.factory import make_embedder, make_llm, make_store
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever

log = logging.getLogger("api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)


def build_pipeline() -> RAGPipeline:
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    config = RetrievalConfig(
        exclude_recitals=True, hybrid=settings.retrieval_hybrid, rerank=settings.retrieval_rerank
    )
    return RAGPipeline(Retriever(embedder, store, config), make_llm(settings))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = build_pipeline()  # loads the BM25 index once, at startup
    # Warm the credential cache and TLS connections so the first user request is not the slow one.
    await asyncio.to_thread(app.state.pipeline.retriever.retrieve, "warm-up", 1)
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


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    pipeline: RAGPipeline = request.app.state.pipeline

    async def events():
        start, first_token = time.perf_counter(), None
        try:
            async for kind, value in pipeline.stream(body.question, body.k):
                if kind == "sources":
                    yield sse("sources", [source_dict(i, rc) for i, rc in enumerate(value, 1)])
                else:
                    first_token = first_token or time.perf_counter() - start
                    yield sse("token", value)
            yield sse("done", {"ttft_s": first_token, "total_s": time.perf_counter() - start})
        except Exception:
            log.exception("chat failed")
            yield sse("error", {"message": "The assistant failed to answer. Try again."})

    # no-cache + X-Accel-Buffering stop proxies from buffering the stream into one blob
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
