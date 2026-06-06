#!/usr/bin/env python3
"""Run the eval suite against a deployed agent and emit a report.

For each question:
  - Run N seeds (default 3).
  - Per-seed pass = answer matches expected_columns / row_count, OR
                    matches expected_refusal.
  - Per-question pass = majority of seeds pass.
Suite passes when:
  - >= --pass-threshold of questions pass.
  - p95 latency regression vs baseline.json <= --latency-budget.
  - average cost regression vs baseline.json <= --cost-budget.

This is a skeleton; wire `_invoke_agent` to your agent's runtime.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


def _invoke_agent(agent_arn: str, question: str) -> tuple[dict[str, Any], float, float]:
    """Stub: invoke the deployed AgentCore runtime with `question`.

    Returns (response, latency_seconds, cost_usd). Replace with a real
    boto3.client('bedrock-agent-runtime').invoke_agent or AgentCore call.
    """
    raise NotImplementedError("wire to your AgentCore client")


def _judge(question: dict[str, Any], response: dict[str, Any]) -> bool:
    if question.get("expected_refusal"):
        text = response.get("text", "").lower()
        return any(kw in text for kw in ("refuse", "i can't", "cannot help"))
    rows = response.get("rows", [])
    if "expected_columns" in question:
        cols = response.get("columns", [])
        if not all(c in cols for c in question["expected_columns"]):
            return False
    n = len(rows)
    if n < question.get("expected_row_count_min", 0):
        return False
    if "expected_row_count_max" in question and n > question["expected_row_count_max"]:
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent-arn", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--baseline", default="evals/baseline.json")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--pass-threshold", type=float, default=0.9)
    p.add_argument("--latency-budget", type=float, default=1.5, help="multiplier vs baseline p95")
    p.add_argument("--cost-budget", type=float, default=1.2, help="multiplier vs baseline avg")
    p.add_argument("--report", default="eval-report.json")
    args = p.parse_args()

    questions = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()]
    baseline = json.loads(Path(args.baseline).read_text()) if Path(args.baseline).exists() else None

    results = []
    for q in questions:
        latencies, costs, passes = [], [], []
        for _ in range(args.seeds):
            t0 = time.time()
            try:
                response, latency, cost = _invoke_agent(args.agent_arn, q["question"])
            except NotImplementedError as e:
                print(f"WIRE-UP REQUIRED: {e}", file=sys.stderr)
                return 2
            latencies.append(latency)
            costs.append(cost)
            passes.append(_judge(q, response))
        results.append({
            "id": q["id"],
            "pass": sum(passes) > args.seeds // 2,
            "latency_p95": max(latencies),
            "cost_avg": statistics.mean(costs),
        })

    suite_pass_rate = sum(r["pass"] for r in results) / len(results)
    suite_latency_p95 = statistics.median([r["latency_p95"] for r in results])  # rough proxy
    suite_cost_avg = statistics.mean([r["cost_avg"] for r in results])

    failures = []
    if suite_pass_rate < args.pass_threshold:
        failures.append(f"pass rate {suite_pass_rate:.2%} < {args.pass_threshold:.2%}")
    if baseline:
        if suite_latency_p95 > baseline["latency_p95"] * args.latency_budget:
            failures.append(f"latency regressed {suite_latency_p95:.2f}s > {baseline['latency_p95'] * args.latency_budget:.2f}s")
        if suite_cost_avg > baseline["cost_avg"] * args.cost_budget:
            failures.append(f"cost regressed ${suite_cost_avg:.4f} > ${baseline['cost_avg'] * args.cost_budget:.4f}")

    Path(args.report).write_text(json.dumps({
        "results": results,
        "summary": {"pass_rate": suite_pass_rate, "latency_p95": suite_latency_p95, "cost_avg": suite_cost_avg},
        "failures": failures,
    }, indent=2))

    if failures:
        for f in failures:
            print(f"EVAL FAILURE: {f}", file=sys.stderr)
        return 1
    print("EVAL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
