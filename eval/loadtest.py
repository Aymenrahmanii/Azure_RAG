"""Closed-loop load test for POST /chat: N virtual users, each asks, reads the whole stream, waits a
think time, asks again. Reports latency percentiles, error mix, throughput, tokens and cost.

    python -m eval.loadtest --url https://<api> --key .keys/private.pem --users 5 --duration 60

THIS SPENDS REAL MONEY against a deployed API (every non-cached request calls the LLM, the agent
several times). Start small. Each virtual user gets its own token, because the rate limit is per
user: with one shared token you would be measuring the rate limiter, not the system.

Client-side TTFT is the time until the first `token` event arrives. In agent mode the answer is one
event, so TTFT is about equal to total time (see docs/experiments.md).
"""

import argparse
import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import settings
from app.observability import estimate_cost
from app.security.mint import mint_token

ANSWERABLE = ("direct", "multi_article", "cross_regulation")


@dataclass
class Result:
    status: int  # HTTP status; 0 = transport error / timeout; -1 = stream ended in an error event
    total: float
    ttft: float | None = None
    tokens: int = 0
    cached: bool = False
    pipeline: str | None = None


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    seconds: float = 0.0


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def parse_event(block: str) -> tuple[str, object] | None:
    lines = block.strip().split("\n")
    if len(lines) != 2 or not lines[0].startswith("event: "):
        return None
    return lines[0][7:], json.loads(lines[1].removeprefix("data: "))


async def one_request(client: httpx.AsyncClient, url: str, token: str, body: dict) -> Result:
    start = time.perf_counter()
    result = Result(status=0, total=0.0)
    try:
        async with client.stream(
            "POST", f"{url}/chat", json=body, headers={"Authorization": f"Bearer {token}"}
        ) as resp:
            result.status = resp.status_code
            if resp.status_code != 200:
                await resp.aread()
            else:
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        event = parse_event(block)
                        if event is None:
                            continue
                        name, data = event
                        if name == "token" and result.ttft is None:
                            result.ttft = time.perf_counter() - start
                        elif name == "route":
                            result.pipeline = data.get("pipeline")  # type: ignore[union-attr]
                        elif name == "error":
                            result.status = -1
                        elif name == "done":
                            result.tokens = data.get("tokens", 0)  # type: ignore[union-attr]
                            result.cached = bool(data.get("cached"))  # type: ignore[union-attr]
    except httpx.HTTPError:
        result.status = 0
    result.total = time.perf_counter() - start
    return result


async def user_loop(i, args, questions, report: Report, deadline: float, key: str) -> None:
    rng = random.Random(i)
    token = mint_token(key, args.issuer, args.audience, f"load-{i}", [], 3600)
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        await asyncio.sleep(rng.uniform(0, args.think))  # de-synchronise the users
        while time.perf_counter() < deadline:
            question = rng.choice(questions)
            body = {"question": question, "mode": args.mode, "k": 7}
            report.results.append(await one_request(client, args.url, token, body))
            await asyncio.sleep(rng.uniform(0.5, 1.5) * args.think)


def summarise(report: Report) -> dict:
    ok = [r for r in report.results if r.status == 200]
    fresh = [r for r in ok if not r.cached]
    codes: dict[str, int] = {}
    for r in report.results:
        codes[str(r.status)] = codes.get(str(r.status), 0) + 1
    totals = [r.total for r in ok]
    ttfts = [r.ttft for r in ok if r.ttft is not None]
    cost = sum(estimate_cost(r.tokens, 0, settings) for r in fresh)  # input price only: a floor
    return {
        "requests": len(report.results),
        "ok": len(ok),
        "status_codes": codes,
        "cache_hits": sum(r.cached for r in ok),
        "rps": round(len(report.results) / report.seconds, 3) if report.seconds else 0,
        "total_s": {f"p{p}": round(percentile(totals, p), 2) for p in (50, 95, 99)},
        "ttft_s": {f"p{p}": round(percentile(ttfts, p), 2) for p in (50, 95, 99)},
        "tokens_per_fresh_request": round(sum(r.tokens for r in fresh) / len(fresh))
        if fresh
        else 0,
        "cost_floor_usd": round(cost, 4),
    }


def load_questions(path: Path) -> list[str]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r["question"] for r in rows if r["type"] in ANSWERABLE]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--key", type=Path, required=True, help="private key used to mint tokens")
    p.add_argument("--issuer", default=settings.auth_issuer or "https://azrag.dev")
    p.add_argument("--audience", default=settings.auth_audience or "azrag-api")
    p.add_argument("--users", type=int, default=3)
    p.add_argument("--duration", type=float, default=60, help="seconds to keep starting requests")
    p.add_argument(
        "--think", type=float, default=2.0, help="mean seconds between a user's requests"
    )
    p.add_argument("--mode", default="agent")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--dataset", type=Path, default=Path("eval/dataset.jsonl"))
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    questions = load_questions(args.dataset)
    key = args.key.read_text()
    report = Report()
    start = time.perf_counter()
    deadline = start + args.duration
    await asyncio.gather(
        *(user_loop(i, args, questions, report, deadline, key) for i in range(args.users))
    )
    report.seconds = time.perf_counter() - start
    summary = {"args": {"users": args.users, "mode": args.mode, "duration": args.duration}}
    summary |= summarise(report)
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
