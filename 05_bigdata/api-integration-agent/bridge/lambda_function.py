"""Bridge Lambda for the api-integration-agent.

Two ways to invoke the underlying AgentCore Runtime:

  1. Direct invoke (sync POST) — pass the same payload the runtime
     entrypoint expects, e.g.
       {"dataset":"jph-todos","api_spec":{"endpoint":"https://...","auth_type":"none"}}

  2. EventBridge "Object Created" event under
     ${REQUEST_PREFIX} (default `api-requests/`). The S3 object body is
     a JSON request document (same shape as direct invoke). Drop
     `api-integration-agent/jph-todos.json` into that prefix and the agent runs.

Env:
    AWS_REGION              AWS region
    RUNTIME_ARN             AgentCore Runtime ARN of api-integration-agent
    GATEWAY_CREDS_SECRET    Secrets Manager secret with Cognito creds
    REQUEST_PREFIX          object-key prefix that triggers a run (e.g.
                            api-requests/)
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
REQUEST_PREFIX = os.environ.get("REQUEST_PREFIX", "api-requests/")

SECRETS = boto3.client("secretsmanager", region_name=REGION)
COGNITO = boto3.client("cognito-idp", region_name=REGION)
S3 = boto3.client("s3", region_name=REGION)


def _bearer_token() -> str:
    secret = SECRETS.get_secret_value(SecretId=GATEWAY_CREDS_SECRET)
    creds = json.loads(secret["SecretString"])
    auth = COGNITO.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


def _invoke_runtime(payload: dict) -> str:
    token = _bearer_token()
    encoded = urllib.parse.quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{encoded}/invocations?qualifier=DEFAULT"
    )
    body = json.dumps(payload).encode("utf-8")
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


def _payload_from_s3(bucket: str, key: str) -> dict:
    obj = S3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()
    return json.loads(raw)


def lambda_handler(event, context):
    if isinstance(event, dict) and event.get("source") == "aws.s3":
        detail = event.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        raw_key = detail.get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(raw_key)
        if not bucket or not key:
            logger.warning("missing bucket/key in event: %s", json.dumps(event)[:1000])
            return {"statusCode": 400, "body": "missing bucket or key"}
        if not key.startswith(REQUEST_PREFIX):
            logger.info("ignoring %s (outside request prefix %s)", key, REQUEST_PREFIX)
            return {"statusCode": 204, "body": "ignored"}
        if not key.endswith(".json"):
            logger.info("ignoring %s (not json)", key)
            return {"statusCode": 204, "body": "ignored"}
        payload = _payload_from_s3(bucket, key)
        logger.info("invoking runtime from s3://%s/%s", bucket, key)
    else:
        payload = event if isinstance(event, dict) else {"prompt": str(event)}
        logger.info("invoking runtime with direct payload")

    output = _invoke_runtime(payload)
    logger.info("runtime output (first 1k): %s", output[:1000])
    return {"statusCode": 200, "body": output}
