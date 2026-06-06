"""Manually invoke the deployed data-quality-deequ-agent runtime with a
design URI.

Usage:
    RUNTIME_ARN=<arn> SECRET_NAME=<secret> AWS_REGION=us-west-2 \
        python invoke_agent.py s3://bucket/designs/violations.json
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import uuid

import boto3
import requests

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ["RUNTIME_ARN"]
SECRET_NAME = os.environ["SECRET_NAME"]


def get_token() -> str:
    secret = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
        SecretId=SECRET_NAME
    )
    creds = json.loads(secret["SecretString"])
    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


def invoke(design_uri: str) -> str:
    token = get_token()
    encoded = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded}"
        f"/invocations?qualifier=DEFAULT"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
    }
    resp = requests.post(
        url, headers=headers, json={"design_uri": design_uri}, timeout=1800
    )
    resp.raise_for_status()
    return resp.text


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: invoke_agent.py s3://bucket/designs/file.json", file=sys.stderr)
        sys.exit(2)
    print(invoke(sys.argv[1]))
