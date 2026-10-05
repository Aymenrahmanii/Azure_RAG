import pytest

from eval.loadtest import Report, Result, parse_event, percentile, summarise


@pytest.mark.parametrize(
    ("p", "expected"), [(50, 5), (95, 10), (99, 10), (10, 1), (0, 1), (100, 10)]
)
def test_percentile_is_nearest_rank(p, expected):
    assert percentile(list(range(10, 0, -1)), p) == expected


def test_percentile_of_nothing_is_zero():
    assert percentile([], 95) == 0.0


def test_parse_event():
    assert parse_event('event: done\ndata: {"tokens": 5}') == ("done", {"tokens": 5})
    assert parse_event('event: token\ndata: "hi"\n') == ("token", "hi")
    assert parse_event(": keep-alive") is None


def test_summary_counts_rate_limits_errors_and_excludes_cache_from_cost():
    report = Report(seconds=10)
    report.results = [
        Result(200, 4.0, 1.0, tokens=1000),
        Result(200, 6.0, 2.0, tokens=3000),
        Result(200, 0.1, 0.1, tokens=0, cached=True),
        Result(429, 0.05),
        Result(0, 120.0),
        Result(-1, 3.0),
    ]
    s = summarise(report)
    assert s["requests"] == 6 and s["ok"] == 3 and s["cache_hits"] == 1
    assert s["status_codes"] == {"200": 3, "429": 1, "0": 1, "-1": 1}
    assert s["tokens_per_fresh_request"] == 2000
    assert s["rps"] == 0.6
    assert s["total_s"]["p50"] == 4.0  # failures do not pollute the latency percentiles
