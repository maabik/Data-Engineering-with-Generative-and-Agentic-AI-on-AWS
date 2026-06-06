#!/usr/bin/env python3
"""Sweep stale Terraform sandbox workspaces.

For each Terraform workspace tagged Owner=<x>, check the most recent apply
(via Terraform state's serial bump). If older than --warn-days, post to
Slack. If older than --destroy-days, run `terraform destroy`.

Idempotent. Default is --dry-run; pass --no-dry-run to act.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3


def list_workspaces(tf_dir: Path) -> list[str]:
    out = subprocess.check_output(["terraform", "workspace", "list"], cwd=tf_dir, text=True)
    return [ws.strip().lstrip("* ").strip() for ws in out.splitlines() if ws.strip() and ws.strip() != "default"]


def workspace_last_modified(state_bucket: str, key_prefix: str, workspace: str) -> datetime | None:
    """The S3 backend stores per-workspace state at env:/<workspace>/<key>."""
    s3 = boto3.client("s3")
    key = f"env:/{workspace}/{key_prefix}"
    try:
        head = s3.head_object(Bucket=state_bucket, Key=key)
    except s3.exceptions.ClientError:
        return None
    return head["LastModified"]


def slack(webhook: str, message: str) -> None:
    if not webhook:
        return
    import urllib.request

    req = urllib.request.Request(
        webhook,
        data=json.dumps({"text": message}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tf-dir", default="reference-pipeline/terraform/envs/sandbox")
    p.add_argument("--state-bucket", required=True)
    p.add_argument("--state-key", default="devops-data-engineering/reference-pipeline/sandbox.tfstate")
    p.add_argument("--warn-days", type=int, default=7)
    p.add_argument("--destroy-days", type=int, default=14)
    p.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK", ""))
    p.add_argument("--no-dry-run", action="store_true")
    args = p.parse_args()

    tf_dir = Path(args.tf_dir).resolve()
    now = datetime.now(timezone.utc)

    for ws in list_workspaces(tf_dir):
        if ws.startswith("ci-") or ws == "default":
            continue
        last = workspace_last_modified(args.state_bucket, args.state_key, ws)
        if last is None:
            continue
        age = now - last
        if age > timedelta(days=args.destroy_days):
            print(f"DESTROY {ws} (age {age.days}d)")
            if args.no_dry_run:
                env = {**os.environ, "TF_WORKSPACE": ws, "TF_VAR_owner": ws}
                subprocess.check_call(["terraform", "destroy", "-auto-approve"], cwd=tf_dir, env=env)
                slack(args.slack_webhook, f":wastebasket: Destroyed stale sandbox `{ws}` (age {age.days}d)")
        elif age > timedelta(days=args.warn_days):
            print(f"WARN    {ws} (age {age.days}d)")
            if args.no_dry_run:
                slack(args.slack_webhook, f":warning: Sandbox `{ws}` idle for {age.days}d. Will destroy at {args.destroy_days}d.")
        else:
            print(f"OK      {ws} (age {age.days}d)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
