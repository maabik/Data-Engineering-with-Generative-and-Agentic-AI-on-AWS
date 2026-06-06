"""Bridge Lambda: design JSON lands in S3 -> AgentCore Runtime invocation.

When the data-architect-agent writes
    s3://${DATA_BUCKET}/${DESIGNS_PREFIX}<file>.json
EventBridge invokes this function. We mint a Cognito access token and POST
{"design_uri":"s3://..."} to the Data Quality (Deequ) Agent's runtime.

Env:
    AWS_REGION              AWS region
    RUNTIME_ARN             AgentCore Runtime ARN of data-quality-deequ-agent
    GATEWAY_CREDS_SECRET    Secrets Manager secret with Cognito creds
    DESIGNS_PREFIX          object-key prefix to filter on (e.g. designs/)
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
DESIGNS_PREFIX = os.environ.get("DESIGNS_PREFIX", "designs/")

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


def _invoke_runtime(design_uri: str) -> str:
    token = _bearer_token()
    encoded = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{encoded}/invocations?qualifier=DEFAULT"
    )
    body = json.dumps({"design_uri": design_uri}).encode("utf-8")
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
    if not key.startswith(DESIGNS_PREFIX):
        logger.info("ignoring %s (outside designs prefix %s)", key, DESIGNS_PREFIX)
        return {"statusCode": 204, "body": "ignored"}
    if not key.endswith(".json"):
        logger.info("ignoring %s (not a .json file)", key)
        return {"statusCode": 204, "body": "ignored"}

    design_uri = f"s3://{bucket}/{key}"
    logger.info("invoking runtime for %s", design_uri)
    output = _invoke_runtime(design_uri)
    logger.info("runtime output (first 1k): %s", output[:1000])
    return {"statusCode": 200, "body": output}
