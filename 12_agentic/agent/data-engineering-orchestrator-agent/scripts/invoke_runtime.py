#!/usr/bin/env python3
"""Direct AgentCore Runtime invocation for the Data Engineering Orchestrator."""

import json
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]


def invoke(prompt: str) -> dict:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = json.dumps({"prompt": prompt}).encode("utf-8")
    response = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=payload,
    )
    return json.loads(response["response"].read())


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        prompt = ""
    if not prompt.strip():
        print("usage: invoke_runtime.py '<request>'  OR  echo '<request>' | invoke_runtime.py", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(invoke(prompt), indent=2))
