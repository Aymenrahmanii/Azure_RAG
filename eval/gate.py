"""Quality gate: compare an eval result file with eval/thresholds.json; exit 1 on any regression.

    python -m eval.gate eval/results/<run>.json --tier retrieval
    python -m eval.gate eval/results/<run>.json --tier generation

Writes a markdown table to stdout (and to $GITHUB_STEP_SUMMARY when set).
"""

import argparse
import json
import os
import sys
from pathlib import Path

THRESHOLDS = Path("eval/thresholds.json")


def lookup(summary: dict, path: str) -> float | None:
    """'overall.recall@all' or 'by_type.global.correctness' -> value, None if absent."""
    node = summary
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, (int, float)) else None


def check(summary: dict, rules: dict) -> list[dict]:
    rows = []
    for metric, bound in rules.items():
        value = lookup(summary, metric)
        if value is None:
            ok, reason = False, "missing from results"
        elif "min" in bound and value < bound["min"]:
            ok, reason = False, f"below minimum {bound['min']}"
        elif "max" in bound and value > bound["max"]:
            ok, reason = False, f"above maximum {bound['max']}"
        else:
            ok, reason = True, ""
        rows.append({"metric": metric, "value": value, "bound": bound, "ok": ok, "reason": reason})
    return rows


def render(rows: list[dict], title: str) -> str:
    lines = [f"### {title}", "", "| metric | value | limit | |", "|---|---|---|---|"]
    for r in rows:
        limit = ", ".join(f"{k} {v}" for k, v in r["bound"].items())
        value = "n/a" if r["value"] is None else f"{r['value']:.3f}"
        mark = "pass" if r["ok"] else f"**FAIL** {r['reason']}"
        lines.append(f"| {r['metric']} | {value} | {limit} | {mark} |")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("results", type=Path)
    p.add_argument("--tier", choices=["retrieval", "generation"], required=True)
    p.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    p.add_argument("--min-questions", type=int, default=50, help="refuse partial runs")
    args = p.parse_args()

    data = json.loads(args.results.read_text(encoding="utf-8"))
    summary = data["summary"]
    rules = json.loads(args.thresholds.read_text(encoding="utf-8"))[args.tier]
    n = summary["overall"]["n"]
    rows = check(summary, rules)
    report = render(rows, f"Eval gate: {args.tier} ({n} questions, {data['config']['label']})")
    if n < args.min_questions:
        report += f"\n\n**FAIL**: only {n} questions were evaluated (need {args.min_questions})."
    print(report)
    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    return 0 if all(r["ok"] for r in rows) and n >= args.min_questions else 1


if __name__ == "__main__":
    sys.exit(main())
