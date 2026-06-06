#!/usr/bin/env python3
"""Re-run the judge against a single past invocation by request id.

Useful for sanity-checking the judge after rubric changes, or for forcing an
evaluation on an unsampled invocation.

Usage:
    python3 replay_to_judge.py <agent_name> <request_id> [--region us-east-1]

Looks up the agent in the registry to get the rubric, fetches the
matching log event(s) from CloudWatch Logs Insights, calls Bedrock with the
exact same prompt the Lambda would have used, and prints the verdict JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import boto3

# Re-use the same prompt builder the Lambda uses so this is a faithful replay.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluator"))
from judge_prompt import SYSTEM_PROMPT, build_user_prompt  # type: ignore

PREFIX = "oae"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agent_name")
    p.add_argument("request_id")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--judge-model", default="us.anthropic.claude-sonnet-4-6")
    args = p.parse_args()

    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"{PREFIX}-{account}-{args.region}"

    s3 = boto3.client("s3", region_name=args.region)
    registry = json.loads(s3.get_object(Bucket=bucket, Key="registry.json")["Body"].read())
    agent = next((a for a in registry["agents"] if a["name"] == args.agent_name), None)
    if not agent:
        print(f"agent {args.agent_name} not in registry", file=sys.stderr)
        return 1

    logs = boto3.client("logs", region_name=agent["region"])
    print(f"  searching {agent['log_group']} for requestId={args.request_id}", file=sys.stderr)
    qid = logs.start_query(
        logGroupName=agent["log_group"],
        startTime=int(time.time()) - 7 * 86400,
        endTime=int(time.time()),
        queryString=f"fields @message | filter @message like /{args.request_id}/ | limit 20",
    )["queryId"]
    while True:
        result = logs.get_query_results(queryId=qid)
        if result["status"] in {"Complete", "Failed", "Cancelled"}:
            break
        time.sleep(1)
    if not result["results"]:
        print("  no matching log events", file=sys.stderr)
        return 1

    prompt, response = "", ""
    for row in result["results"]:
        msg = next((c["value"] for c in row if c["field"] == "@message"), "")
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            continue
        if data.get("phase") == "response" or ("input" in data and "output" in data):
            payload = data.get("payload") or data
            prompt = prompt or payload.get("input") or payload.get("prompt") or ""
            response = response or payload.get("output") or payload.get("response") or ""

    if not response:
        print("  could not parse a response from log events", file=sys.stderr)
        return 1

    bedrock = boto3.client("bedrock-runtime", region_name=args.region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 800,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_user_prompt(
            agent_name=agent["name"],
            agent_description=agent.get("description"),
            user_input=prompt,
            agent_output=response,
        )}],
        "temperature": 0.0,
    }
    resp = bedrock.invoke_model(
        modelId=args.judge_model,
        body=json.dumps(body).encode(),
        accept="application/json",
        contentType="application/json",
    )
    raw = json.loads(resp["body"].read())
    text = raw["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]

    print(json.dumps(json.loads(text), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
