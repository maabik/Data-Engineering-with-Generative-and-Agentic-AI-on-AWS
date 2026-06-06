"""AgentCore Gateway target for the SQL Evaluations Agent.

The Gateway invokes this Lambda when its MCP tool `evaluate_sql` is called.
The Lambda turns around and invokes the AgentCore Runtime that hosts the
actual Strands agent, passing through `sql`, `context`, and `dialect`.

Environment:
  AGENT_RUNTIME_ARN   ARN of the SQL eval AgentCore Runtime
"""

import json
import os
import urllib.parse
from typing import Any, Dict

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]

agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


def _evaluate_sql(args: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "sql": args.get("sql") or "",
        "context": args.get("context") or "",
        "dialect": args.get("dialect") or "ansi",
    }
    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = response["response"].read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"status": "rejected", "raw": body, "reasoning": "runtime returned non-JSON"}


def lambda_handler(event, _context):
    # Gateway delivers the tool arguments at the event root; the target only
    # advertises one tool (`evaluate_sql`), so route unconditionally.
    return _evaluate_sql(event)
