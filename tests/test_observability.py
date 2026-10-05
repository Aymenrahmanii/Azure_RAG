import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import observability
from app.core.config import Settings
from app.providers.openai_compat import parse_stream_usage


def test_cost_estimate_uses_per_million_prices():
    cfg = Settings(price_input_per_mtok=1.0, price_output_per_mtok=4.0)
    assert observability.estimate_cost(500_000, 250_000, cfg) == pytest.approx(1.5)
    assert observability.estimate_cost(0, 0, cfg) == 0


def test_setup_is_a_noop_without_connection_string():
    assert observability.setup(Settings(applicationinsights_connection_string="")) is False


def test_stage_records_a_span_and_marks_errors():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real = observability.tracer
    observability.tracer = provider.get_tracer("test")
    try:
        with observability.stage("retrieve.dense", k=7):
            pass
        with pytest.raises(ValueError):
            with observability.stage("llm.chat"):
                raise ValueError("boom")
    finally:
        observability.tracer = real
    ok, failed = exporter.get_finished_spans()
    assert ok.name == "rag.retrieve.dense" and ok.attributes["k"] == 7
    assert failed.status.status_code == trace.StatusCode.ERROR
    assert any(e.name == "exception" for e in failed.events)


def test_stream_usage_is_read_from_the_final_chunk():
    final = "data: " + json.dumps(
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30}}
    )
    assert parse_stream_usage(final) == {"prompt_tokens": 120, "completion_tokens": 30}
    assert parse_stream_usage("data: " + json.dumps({"choices": [{"delta": {}}]})) is None
    assert parse_stream_usage("data: [DONE]") is None
    assert parse_stream_usage("") is None
