"""OpenTelemetry for the API: traces, metrics and a cost estimate per request.

Without APPLICATIONINSIGHTS_CONNECTION_STRING everything here is a no-op (the OpenTelemetry API
returns inert tracers and meters), so local runs and tests need no setup. With it, the Azure Monitor
distro exports requests, dependencies, our spans and our metrics to Application Insights.

Metrics are low-cardinality on purpose (pipeline, status, stage): never put a user id or a question
in a metric dimension. Spans may carry more detail, but still no question text (see audit.py).
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import metrics, trace

from app.core.config import Settings

log = logging.getLogger("observability")

tracer = trace.get_tracer("azrag")
_meter = metrics.get_meter("azrag")

requests_total = _meter.create_counter("rag.requests", description="chat requests")
request_seconds = _meter.create_histogram("rag.request.duration", unit="s")
ttft_seconds = _meter.create_histogram("rag.ttft", unit="s", description="time to first token")
stage_seconds = _meter.create_histogram("rag.stage.duration", unit="s")
tokens_total = _meter.create_counter("rag.tokens", description="LLM tokens spent")
llm_calls_total = _meter.create_counter("rag.llm.calls")
cost_usd_total = _meter.create_counter("rag.cost.usd", unit="USD")
cache_total = _meter.create_counter("rag.cache", description="semantic cache lookups")


def setup(cfg: Settings) -> bool:
    """Start exporting to Application Insights. Returns True when export is on."""
    if not cfg.applicationinsights_connection_string:
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor  # heavy import: only when used

    configure_azure_monitor(
        connection_string=cfg.applicationinsights_connection_string,
        # Azure SDK HTTP calls log every request at INFO, and Log Analytics bills by ingestion.
        logger_name="api",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)
    return True


@contextmanager
def stage(name: str, **attrs) -> Iterator[trace.Span]:
    """A span plus a duration sample, for one stage of a request (retrieve, llm, tool, ...)."""
    start = time.perf_counter()
    with tracer.start_as_current_span(f"rag.{name}", attributes=attrs) as span:
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR)
            raise
        finally:
            stage_seconds.record(time.perf_counter() - start, {"stage": name})


def estimate_cost(prompt_tokens: int, completion_tokens: int, cfg: Settings) -> float:
    """USD for one request's LLM tokens. Prices are per million tokens and come from settings:
    they are an assumption to check against the Azure price sheet, not something the API knows."""
    return (
        prompt_tokens * cfg.price_input_per_mtok + completion_tokens * cfg.price_output_per_mtok
    ) / 1_000_000


def record_request(
    *,
    pipeline: str,
    status: str,
    seconds: float,
    ttft: float | None,
    prompt_tokens: int,
    completion_tokens: int,
    llm_calls: int,
    cost: float,
    cached: bool = False,
) -> None:
    dims = {"pipeline": pipeline or "none", "status": status, "cached": cached}
    requests_total.add(1, dims)
    request_seconds.record(seconds, dims)
    if ttft is not None:
        ttft_seconds.record(ttft, dims)
    tokens_total.add(prompt_tokens, {"pipeline": dims["pipeline"], "direction": "prompt"})
    tokens_total.add(completion_tokens, {"pipeline": dims["pipeline"], "direction": "completion"})
    llm_calls_total.add(llm_calls, {"pipeline": dims["pipeline"]})
    cost_usd_total.add(cost, {"pipeline": dims["pipeline"]})
