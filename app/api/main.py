"""FastAPI app. POST /chat streams Server-Sent Events:

route    which pipeline answers (auto mode: after a one-call classification)
notice   personal data was found in the question and masked before processing
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
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import observability
from app.core.config import Settings, settings
from app.graph.loader import load_graph
from app.graph.search import GlobalSearch, GraphConfig, GraphRetriever
from app.providers.factory import make_embedder, make_llm, make_store
from app.providers.openai_compat import Usage, usage_var
from app.rag.agent import Agent
from app.rag.cache import CachedAnswer, MemoEmbedder, SemanticCache, scope_key
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval import RetrievalConfig, Retriever
from app.rag.router import PipelineRunner, Router
from app.security import audit
from app.security.access import AccessPolicy, reset_visible, set_visible
from app.security.auth import TokenError, TokenVerifier, User
from app.security.pii import redact_pii
from app.security.ratelimit import SlidingWindowLimiter
from app.security.sanitize import StreamSanitizer, sanitize_output

log = logging.getLogger("api")
Mode = Literal["auto", "baseline", "graph", "agent", "global"]
BLOCKED_PREFIX = "Request blocked"  # content-filter replies are never cached


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=7, ge=1, le=20)
    mode: Mode = "agent"  # "auto" (router) did not beat always-agent: see docs/experiments.md


@dataclass
class Services:
    baseline: RAGPipeline
    router: Router
    graph: RAGPipeline | None = None
    agent: Agent | None = None
    global_search: GlobalSearch | None = None
    auth: TokenVerifier | None = None  # None only in local development
    policy: AccessPolicy = field(default_factory=AccessPolicy)
    limiter: SlidingWindowLimiter = field(default_factory=lambda: SlidingWindowLimiter(0))
    all_sources: frozenset[str] = frozenset()
    audit_salt: str = ""
    cache: SemanticCache | None = None

    def available(self) -> list[str]:
        have = {
            "baseline": self.baseline,
            "graph": self.graph,
            "agent": self.agent,
            "global": self.global_search,
        }
        return [name for name, svc in have.items() if svc is not None]


def security_from_settings(
    cfg: Settings,
) -> tuple[TokenVerifier | None, AccessPolicy, SlidingWindowLimiter]:
    """Fails closed: outside `local`, running without authentication is a startup error."""
    if cfg.auth_mode == "jwt":
        auth = TokenVerifier(
            cfg.auth_issuer, cfg.auth_audience, cfg.auth_public_key, cfg.auth_jwks_url
        )
    elif cfg.environment == "local":
        auth = None
    else:
        raise RuntimeError(
            f"AUTH_MODE=jwt is required when ENVIRONMENT={cfg.environment!r}; refusing to start"
        )
    policy = AccessPolicy.from_json(cfg.acl_restricted)
    if policy.restricted and auth is None:
        raise RuntimeError("ACL_RESTRICTED is set but authentication is off: it cannot be enforced")
    return auth, policy, SlidingWindowLimiter(cfg.rate_limit_per_minute)


def build_services() -> Services:
    auth, policy, limiter = security_from_settings(settings)
    embedder = make_embedder(settings.embedding_model, settings)
    store = make_store(settings, embedder, settings.embedding_model)
    config = RetrievalConfig(
        exclude_recitals=True, hybrid=settings.retrieval_hybrid, rerank=settings.retrieval_rerank
    )
    memo = MemoEmbedder(embedder)  # the cache lookup and retrieval share one query embedding
    retriever = Retriever(memo, store, config)
    sec = {
        "auth": auth,
        "policy": policy,
        "limiter": limiter,
        "all_sources": frozenset(c.metadata["source"] for c in retriever.chunks),
        "audit_salt": settings.audit_salt,
        "cache": SemanticCache(
            lambda q: memo.embed([q])[0],
            settings.cache_threshold,
            settings.cache_ttl_seconds,
            settings.cache_max_entries,
        )
        if settings.cache_enabled
        else None,
    }
    llm = make_llm(settings)
    baseline = RAGPipeline(retriever, llm)
    graph = load_graph(settings)
    if graph is None or llm is None:
        log.warning("running without graph features (graph=%s, llm=%s)", graph, llm)
        return Services(baseline, Router(llm, {"baseline": PipelineRunner(baseline, 7)}), **sec)
    graph_pipeline = RAGPipeline(GraphRetriever(retriever, graph, GraphConfig(n_graph=2)), llm)
    agent = Agent(llm, retriever, graph)
    global_search = GlobalSearch(llm, graph, embedder=embedder) if graph.communities() else None
    runners = {"baseline": PipelineRunner(baseline, 7), "agent": agent}
    return Services(baseline, Router(llm, runners), graph_pipeline, agent, global_search, **sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.services = build_services()  # loads the BM25 index and graph once, at startup
    # Warm the credential cache and TLS connections so the first user request is not the slow one.
    await asyncio.to_thread(app.state.services.baseline.retriever.retrieve, "warm-up", 1)
    yield


telemetry_on = observability.setup(settings)  # before FastAPI() so the app is auto-instrumented
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
        "text": sanitize_output(rc.chunk.text),
        "score": rc.score,
    }


def sources_event(sources) -> str:
    return sse("sources", [source_dict(i, rc) for i, rc in enumerate(sources, 1)])


DEV_USER = User("dev", frozenset({"dev"}))  # only reachable with AUTH_MODE=off, i.e. local
bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> User:
    svc: Services = request.app.state.services
    if svc.auth is None:
        return DEV_USER
    challenge = {"WWW-Authenticate": "Bearer"}
    if creds is None:
        raise HTTPException(401, "authentication required", headers=challenge)
    try:
        return svc.auth.verify(creds.credentials)
    except TokenError as exc:
        log.warning("rejected token: %s", exc)  # the reason stays in the log, not in the response
        raise HTTPException(401, "invalid or expired token", headers=challenge) from exc


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'")
    return response


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/pipelines")
async def pipelines(request: Request, user: User = Depends(current_user)) -> dict:
    return {"available": request.app.state.services.available()}


async def choose(svc: Services, mode: str, question: str) -> tuple[str, str | None]:
    """(pipeline, question kind). Unavailable pipelines fall back to the baseline."""
    if mode != "auto":
        return (mode if mode in svc.available() else "baseline"), None
    kind, _ = await svc.router.classify(question)
    name = svc.router.table.get(kind, svc.router.default)
    return (name if name in svc.available() else "baseline"), kind


@app.post("/chat")
async def chat(
    body: ChatRequest, request: Request, user: User = Depends(current_user)
) -> StreamingResponse:
    svc: Services = request.app.state.services
    # Data minimisation: identifiers the user pasted never reach search, the LLM or the logs.
    # Everything below, including audit hashes, uses the masked question only.
    question, pii = redact_pii(body.question)
    allowed, retry_after = svc.limiter.check(user.id)
    if not allowed:
        audit.record(user_id=user.id, question=question, status="rate_limited", salt=svc.audit_salt)
        raise HTTPException(
            429, "rate limit exceeded", headers={"Retry-After": str(int(retry_after) + 1)}
        )

    async def events():
        start, first_token = time.perf_counter(), None
        meter = Usage()
        usage_var.set(meter)  # everything this request spends on the LLM is counted here
        # Security trimming: which documents this caller may see, for every retrieval path below.
        visible = svc.policy.visible_sources(user.groups, svc.all_sources) if svc.auth else None
        visible_token = set_visible(visible)
        name = kind = None
        cached, answer_text = False, ""
        returned: list = []
        status = "ok"
        try:
            scope = scope_key(visible, body.mode, body.k)
            hit = None
            if svc.cache is not None:
                with observability.stage("cache.lookup"):
                    hit, similarity = await asyncio.to_thread(svc.cache.lookup, scope, question)
                observability.cache_total.add(1, {"result": "hit" if hit else "miss"})
            if hit:
                cached, name, kind, returned = True, hit.pipeline, hit.kind, hit.sources
                yield sse("route", {"pipeline": name, "kind": kind, "cached": True})
            else:
                name, kind = await choose(svc, body.mode, question)
                yield sse("route", {"pipeline": name, "kind": kind})
            if pii:
                masked = ", ".join(f"{label} x{n}" for label, n in sorted(pii.items()))
                yield sse(
                    "notice", {"message": f"Personal data was masked in your question: {masked}"}
                )

            if hit:
                yield sources_event(hit.sources)
                first_token = time.perf_counter() - start
                answer_text = hit.text
                yield sse("token", hit.text)
            elif name in ("baseline", "graph"):
                pipeline = svc.graph if name == "graph" else svc.baseline
                safe = StreamSanitizer()  # no links or images in answers, even split across tokens
                async for what, value in pipeline.stream(question, body.k):
                    if what == "sources":
                        returned = value
                        yield sources_event(value)
                    elif piece := safe.feed(value):
                        first_token = first_token or time.perf_counter() - start
                        answer_text += piece
                        yield sse("token", piece)
                if tail := safe.flush():
                    answer_text += tail
                    yield sse("token", tail)
            else:
                steps: asyncio.Queue = asyncio.Queue()
                runner = svc.agent if name == "agent" else svc.global_search
                work = (
                    asyncio.create_task(runner.run(question, on_step=steps.put))
                    if name == "agent"
                    else asyncio.create_task(runner.run(question))
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
                returned = answer.sources
                yield sources_event(answer.sources)
                first_token = time.perf_counter() - start
                answer_text = sanitize_output(answer.text)
                yield sse("token", answer_text)

            if (
                svc.cache is not None
                and not cached
                and answer_text
                and not answer_text.startswith(BLOCKED_PREFIX)
            ):
                svc.cache.store(scope, question, CachedAnswer(name, kind, answer_text, returned))
            done = {
                "ttft_s": first_token,
                "total_s": time.perf_counter() - start,
                "tokens": meter.prompt_tokens + meter.completion_tokens,
                "llm_calls": meter.calls,
                "cached": cached,
            }
            yield sse("done", done)
        except Exception:
            status = "error"
            log.exception("chat failed")
            yield sse("error", {"message": "The assistant failed to answer. Try again."})
        finally:
            seconds = time.perf_counter() - start
            observability.record_request(
                pipeline=name or "none",
                status=status,
                seconds=seconds,
                ttft=first_token,
                prompt_tokens=meter.prompt_tokens,
                completion_tokens=meter.completion_tokens,
                llm_calls=meter.calls,
                cost=observability.estimate_cost(
                    meter.prompt_tokens, meter.completion_tokens, settings
                ),
                cached=cached,
            )
            audit.record(
                user_id=user.id,
                question=question,
                status="cache_hit" if cached and status == "ok" else status,
                pipeline=name,
                kind=kind,
                sections=[
                    f"{r.chunk.metadata.get('source')}:{r.chunk.metadata.get('section')}"
                    for r in returned
                ],
                tokens=meter.prompt_tokens + meter.completion_tokens,
                llm_calls=meter.calls,
                seconds=seconds,
                pii=pii,
                salt=svc.audit_salt,
            )
            reset_visible(visible_token)

    # no-cache + X-Accel-Buffering stop proxies from buffering the stream into one blob
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
