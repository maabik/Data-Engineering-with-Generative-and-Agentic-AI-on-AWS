#!/usr/bin/env python3
"""Direct AgentCore Runtime invocation for the SQL Evaluations Agent."""

import json
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]


def invoke(sql: str, context: str = "", dialect: str = "ansi") -> dict:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = json.dumps({"sql": sql, "context": context, "dialect": dialect}).encode("utf-8")
    response = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=payload,
    )
    return json.loads(response["response"].read())


if __name__ == "__main__":
    sql = sys.stdin.read() if not sys.stdin.isatty() else (sys.argv[1] if len(sys.argv) > 1 else "")
    if not sql.strip():
        print("usage: invoke_runtime.py '<SQL>'  OR  echo '<SQL>' | invoke_runtime.py", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(invoke(sql), indent=2))
