import json

from app.providers.openai_compat import parse_stream_line


def line(payload: dict) -> str:
    return "data: " + json.dumps(payload)


def test_content_delta():
    assert parse_stream_line(line({"choices": [{"delta": {"content": "Hi"}}]})) == "Hi"


def test_azure_prompt_filter_chunk_has_no_choices():
    assert parse_stream_line(line({"choices": [], "prompt_filter_results": []})) is None


def test_role_only_and_finish_chunks():
    assert parse_stream_line(line({"choices": [{"delta": {"role": "assistant"}}]})) is None
    assert parse_stream_line(line({"choices": [{"delta": {}, "finish_reason": "stop"}]})) is None


def test_done_and_non_data_lines():
    assert parse_stream_line("data: [DONE]") is None
    assert parse_stream_line("") is None
    assert parse_stream_line(": keep-alive") is None
