#!/usr/bin/env python3
"""Discover every active Bedrock agent in this account.

Emits a JSON registry to stdout (and optionally uploads it to S3) describing:
  - every AgentCore Runtime in each target region, READY status only
  - every classic Bedrock Agent in PREPARED status
  - the CloudWatch log group for each one (created if missing)

The registry is consumed by the evaluator Lambda to map an inbound log group
back to agent metadata.

Usage:
    python3 discover_agents.py                # print registry
    python3 discover_agents.py --upload BUCKET --key registry.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGIONS = ["us-east-1", "us-west-2"]
LOG_RETENTION_DAYS = 30


def discover(regions: list[str]) -> dict[str, Any]:
    by_log_group: dict[str, dict[str, Any]] = {}
    agents: list[dict[str, Any]] = []

    for region in regions:
        agents.extend(_agentcore_runtimes(region, by_log_group))
        agents.extend(_bedrock_agents(region, by_log_group))

    return {"agents": agents, "by_log_group": by_log_group}


def _agentcore_runtimes(region: str, by_log_group: dict[str, Any]) -> list[dict[str, Any]]:
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    logs_client = boto3.client("logs", region_name=region)
    out: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("list_agent_runtimes")
    except (ClientError, KeyError):
        return out

    for page in paginator.paginate():
        for rt in page.get("agentRuntimes", []):
            if rt.get("status") != "READY":
                continue
            name = rt["agentRuntimeName"]
            arn = rt["agentRuntimeArn"]
            # Runtime ARN ends with "/runtime/<runtimeId>"; AgentCore writes its
            # invocation logs to "/aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT".
            runtime_id = arn.rsplit("/", 1)[-1]
            log_group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"
            _ensure_log_group(logs_client, log_group)
            entry = {
                "name": name,
                "type": "agentcore_runtime",
                "arn": arn,
                "runtime_id": runtime_id,
                "region": region,
                "log_group": log_group,
                "description": rt.get("description"),
            }
            out.append(entry)
            by_log_group[log_group] = entry

    return out


def _bedrock_agents(region: str, by_log_group: dict[str, Any]) -> list[dict[str, Any]]:
    """Classic Bedrock Agents (bedrock-agent control plane)."""
    try:
        client = boto3.client("bedrock-agent", region_name=region)
        logs_client = boto3.client("logs", region_name=region)
    except ClientError:
        return []

    out: list[dict[str, Any]] = []
    try:
        paginator = client.get_paginator("list_agents")
    except (ClientError, KeyError):
        return out

    for page in paginator.paginate():
        for a in page.get("agentSummaries", []):
            if a.get("agentStatus") != "PREPARED":
                continue
            name = a["agentName"]
            agent_id = a["agentId"]
            log_group = f"/aws/bedrock/agents/{agent_id}"
            _ensure_log_group(logs_client, log_group)
            entry = {
                "name": name,
                "type": "bedrock_agent",
                "agent_id": agent_id,
                "region": region,
                "log_group": log_group,
                "description": a.get("description"),
            }
            out.append(entry)
            by_log_group[log_group] = entry

    return out


def _ensure_log_group(logs_client, name: str) -> None:
    try:
        logs_client.create_log_group(logGroupName=name)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass
    except ClientError as e:
        # AccessDeniedException is fine if the group already exists in another
        # account-managed namespace; subscription will fail loudly later.
        if e.response["Error"]["Code"] not in {"AccessDeniedException", "ResourceAlreadyExistsException"}:
            print(f"  warn: could not ensure log group {name}: {e}", file=sys.stderr)
            return
    try:
        logs_client.put_retention_policy(logGroupName=name, retentionInDays=LOG_RETENTION_DAYS)
    except ClientError:
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--regions", nargs="+", default=DEFAULT_REGIONS)
    p.add_argument("--upload", help="S3 bucket to upload registry.json to")
    p.add_argument("--key", default="registry.json")
    args = p.parse_args()

    registry = discover(args.regions)

    if args.upload:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=args.upload,
            Key=args.key,
            Body=json.dumps(registry, indent=2).encode(),
            ContentType="application/json",
        )
        print(f"registry uploaded -> s3://{args.upload}/{args.key}", file=sys.stderr)

    print(json.dumps(registry, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
