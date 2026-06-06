"""Bridge Lambda: S3 PUT (via EventBridge) -> AgentCore Runtime invocation.

When a new file lands in s3://${BUCKET}/${LANDING_PREFIX}, EventBridge invokes
this function. We mint a Cognito access token and POST to the AgentCore
Runtime invocations URL with {"s3_uri": "s3://..."}.

Env:
    REGION                  AWS region
    RUNTIME_ARN             AgentCore Runtime ARN
    GATEWAY_CREDS_SECRET    Secrets Manager secret with Cognito creds
    LANDING_PREFIX          object-key prefix to filter on (e.g. landing/)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
import uuid

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_ARN = os.environ["RUNTIME_ARN"]
GATEWAY_CREDS_SECRET = os.environ["GATEWAY_CREDS_SECRET"]
LANDING_PREFIX = os.environ.get("LANDING_PREFIX", "landing/")

SECRETS = boto3.client("secretsmanager", region_name=REGION)
COGNITO = boto3.client("cognito-idp", region_name=REGION)


def _bearer_token() -> str:
    secret = SECRETS.get_secret_value(SecretId=GATEWAY_CREDS_SECRET)
    creds = json.loads(secret["SecretString"])
    auth = COGNITO.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _invoke_runtime(s3_uri: str) -> str:
    token = _bearer_token()
    encoded = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{encoded}/invocations?qualifier=DEFAULT"
    )
    body = json.dumps({"s3_uri": s3_uri}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        },
    )
    with urllib.request.urlopen(req, timeout=890) as resp:
        return resp.read().decode("utf-8", errors="replace")


def lambda_handler(event, context):
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name")
    raw_key = detail.get("object", {}).get("key", "")
    key = urllib.parse.unquote_plus(raw_key)

    if not bucket or not key:
        logger.warning("missing bucket/key in event: %s", json.dumps(event)[:1000])
        return {"statusCode": 400, "body": "missing bucket or key"}
    if not key.startswith(LANDING_PREFIX):
        logger.info("ignoring %s (outside landing prefix %s)", key, LANDING_PREFIX)
        return {"statusCode": 204, "body": "ignored"}

    s3_uri = f"s3://{bucket}/{key}"
    logger.info("invoking runtime for %s", s3_uri)
    output = _invoke_runtime(s3_uri)
    logger.info("runtime output (first 1k): %s", output[:1000])
    return {"statusCode": 200, "body": output}
