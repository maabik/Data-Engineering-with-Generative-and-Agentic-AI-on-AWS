"""Generate AWS Step Functions state machines from natural-language prompts.

Triggered by S3 PutObject events on a prompt key. The prompt is sent to
Amazon Bedrock (Claude Messages API), the model output is validated as
Amazon States Language JSON, the definition is written back to S3, and a
Step Functions state machine is created or updated.

Configuration is via environment variables (set by deploy.sh):
    BEDROCK_MODEL_ID            Inference profile / model id
    STATE_MACHINE_ROLE_ARN      Role assumed by the state machine
    STATE_MACHINE_NAME_PREFIX   Prefix for created state machines
    OUTPUT_PREFIX               S3 prefix for generated definitions
    INPUT_PREFIX                S3 prefix the Lambda accepts events from
    ALLOWED_TASK_RESOURCE_PREFIXES  Comma-separated allowlist for Task `Resource`

Security posture:
    * S3 events outside INPUT_PREFIX are rejected, breaking the recursion loop.
    * Model output is parsed and structurally validated before deployment.
    * Task `Resource` ARNs/URIs are checked against an allowlist.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
SFN = boto3.client("stepfunctions", config=_CFG)
S3 = boto3.client("s3", config=_CFG)
BEDROCK = boto3.client("bedrock-runtime", config=_CFG)

MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
ROLE_ARN = os.environ["STATE_MACHINE_ROLE_ARN"]
NAME_PREFIX = os.environ.get("STATE_MACHINE_NAME_PREFIX", "GeneratedSM")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "generated/").rstrip("/") + "/"
INPUT_PREFIX = os.environ.get("INPUT_PREFIX", "prompts/").rstrip("/") + "/"
ALLOWED_RESOURCE_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get(
        "ALLOWED_TASK_RESOURCE_PREFIXES",
        "arn:aws:states:::glue:,arn:aws:states:::lambda:,arn:aws:states:::aws-sdk:",
    ).split(",")
    if p.strip()
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4000"))

_NAME_SAFE = re.compile(r"[^A-Za-z0-9-_]")
_SYSTEM_PROMPT = (
    "You generate AWS Step Functions state machine definitions in Amazon States "
    "Language (ASL). Return ONLY a single JSON object - no prose, no markdown "
    "fences. The JSON must have top-level keys 'StartAt' and 'States'. Every "
    "Task state's 'Resource' must use AWS service integration ARNs "
    "(arn:aws:states:::<service>:...). Do not invent IAM roles, account IDs, or "
    "endpoints - leave placeholders the operator can fill."
)


def _name_from_key(key: str) -> str:
    base = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    safe = _NAME_SAFE.sub("-", base)[:40] or "prompt"
    return f"{NAME_PREFIX}-{safe}"


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("model output contained no JSON object")
    return text[start : end + 1]


def _validate_definition(definition: dict[str, Any]) -> None:
    if not isinstance(definition, dict):
        raise ValueError("definition must be a JSON object")
    for key in ("StartAt", "States"):
        if key not in definition:
            raise ValueError(f"definition missing required key '{key}'")
    states = definition["States"]
    if not isinstance(states, dict) or not states:
        raise ValueError("'States' must be a non-empty object")
    if definition["StartAt"] not in states:
        raise ValueError("'StartAt' references an undefined state")
    for name, state in states.items():
        if not isinstance(state, dict) or "Type" not in state:
            raise ValueError(f"state '{name}' missing 'Type'")
        if state["Type"] == "Task":
            resource = state.get("Resource", "")
            if not isinstance(resource, str) or not resource.startswith(
                ALLOWED_RESOURCE_PREFIXES
            ):
                raise ValueError(
                    f"state '{name}' Resource '{resource}' not in allowlist "
                    f"{ALLOWED_RESOURCE_PREFIXES}"
                )


def _invoke_bedrock(prompt: str) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = BEDROCK.invoke_model(
        modelId=MODEL_ID,
        accept="application/json",
        contentType="application/json",
        body=json.dumps(body).encode("utf-8"),
    )
    payload = json.loads(resp["body"].read())
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            return block.get("text", "")
    raise RuntimeError(f"unexpected Bedrock response shape: {payload!r}")


def _create_or_update(name: str, definition_json: str) -> str:
    try:
        resp = SFN.create_state_machine(
            name=name, definition=definition_json, roleArn=ROLE_ARN
        )
        return resp["stateMachineArn"]
    except SFN.exceptions.StateMachineAlreadyExists:
        sts = boto3.client("sts")
        account = sts.get_caller_identity()["Account"]
        region = boto3.session.Session().region_name
        arn = f"arn:aws:states:{region}:{account}:stateMachine:{name}"
        SFN.update_state_machine(
            stateMachineArn=arn, definition=definition_json, roleArn=ROLE_ARN
        )
        return arn


def lambda_handler(event, context):
    try:
        record = event["Records"][0]
        bucket_name = record["s3"]["bucket"]["name"]
        prompt_key = record["s3"]["object"]["key"]
    except (KeyError, IndexError) as e:
        logger.exception("malformed S3 event")
        return {"Success": False, "Error": f"bad event: {e}"}

    if not prompt_key.startswith(INPUT_PREFIX):
        logger.info("ignoring %s outside INPUT_PREFIX %s", prompt_key, INPUT_PREFIX)
        return {"Success": True, "Skipped": True, "Key": prompt_key}

    try:
        prompt = (
            S3.get_object(Bucket=bucket_name, Key=prompt_key)["Body"]
            .read()
            .decode("utf-8")
        )
    except ClientError:
        logger.exception("failed to read prompt s3://%s/%s", bucket_name, prompt_key)
        raise

    raw = _invoke_bedrock(prompt)
    try:
        definition = json.loads(_extract_json(raw))
        _validate_definition(definition)
    except ValueError as e:
        logger.error("model output rejected: %s\nraw=%s", e, raw[:1000])
        return {"Success": False, "Error": f"validation failed: {e}"}

    definition_json = json.dumps(definition, separators=(",", ":"))
    name = _name_from_key(prompt_key)
    out_key = f"{OUTPUT_PREFIX}{name}.json"
    S3.put_object(
        Bucket=bucket_name,
        Key=out_key,
        Body=definition_json.encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )

    arn = _create_or_update(name, definition_json)
    logger.info(
        "state machine ready arn=%s definition=s3://%s/%s", arn, bucket_name, out_key
    )

    return {
        "Success": True,
        "StateMachineArn": arn,
        "DefinitionKey": out_key,
        "Bucket": bucket_name,
    }
