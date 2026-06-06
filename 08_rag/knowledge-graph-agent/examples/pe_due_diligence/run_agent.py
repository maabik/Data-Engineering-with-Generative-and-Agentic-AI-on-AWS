#!/usr/bin/env python3
"""Run the KG agent against the PE dataroom.

Two modes:
  - ``--remote`` (default): hits the deployed AgentCore Runtime via the
    Cognito-secured MCP server. Requires ``/kg/mcp/agent_arn`` and
    ``/kg/mcp/credentials`` in the local AWS account.
  - ``--local``: bypasses the MCP server and runs the agent inline,
    talking directly to the Neptune graph and vector store. Useful for
    iterating on prompts without redeploying the runtime.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledge_graph_agent.agent.knowledge_graph_agent import run_local


import re

SAMPLE_QUESTIONS = (Path(__file__).parent / "sample_questions.md").read_text()


def _extract_questions(markdown: str) -> list[str]:
    """Pull every ``**...**`` block out of the sample-questions file.

    Questions wrap across multiple lines in the source markdown, so a
    line-by-line match misses them. The ``re.DOTALL`` flag makes ``.``
    span newlines.
    """
    pattern = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
    return [
        # collapse internal whitespace so the prompt is one clean line
        " ".join(match.split())
        for match in pattern.findall(markdown)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", help="Question to ask. Reads stdin if omitted.")
    parser.add_argument(
        "--mode", choices=["local", "remote"], default="local",
        help="local — call the Strands agent inline; remote — invoke the deployed runtime.",
    )
    parser.add_argument(
        "--all-samples", action="store_true",
        help="Run all five sample questions sequentially.",
    )
    args = parser.parse_args()

    if args.all_samples:
        for question in _extract_questions(SAMPLE_QUESTIONS):
            print(f"\n🟦 {question}")
            print("-" * 70)
            print(_answer(question, mode=args.mode))
        return 0

    question = args.question or sys.stdin.read().strip()
    if not question:
        print("❌ no question provided")
        return 1
    print(_answer(question, mode=args.mode))
    return 0


def _answer(question: str, *, mode: str) -> str:
    if mode == "local":
        return run_local(question)
    # Remote mode: invoke the deployed Strands agent runtime, which
    # internally calls the MCP server. /kg/agent/agent_arn is the agent
    # runtime; /kg/mcp/agent_arn is the MCP server it calls.
    import json
    import os
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    agent_arn = ssm.get_parameter(Name="/kg/agent/agent_arn")["Parameter"]["Value"]
    runtime = boto3.client("bedrock-agentcore", region_name=region)
    response = runtime.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": question}).encode("utf-8"),
    )
    body = response["response"].read().decode("utf-8")
    # The runtime returns the agent's text wrapped in a JSON string
    # (the BedrockAgentCoreApp default). Try to unwrap; fall back to raw.
    try:
        parsed = json.loads(body)
        if isinstance(parsed, str):
            return parsed
        return body
    except (json.JSONDecodeError, TypeError):
        return body


if __name__ == "__main__":
    sys.exit(main())
