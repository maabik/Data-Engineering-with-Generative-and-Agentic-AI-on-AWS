"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
api-integration-agent.

The agent receives a desired dataset (and optionally an API spec or a
Secrets Manager secret containing API endpoint + credentials + docs)
and produces a fully deployed AWS Lambda that pulls data from the API
on an EventBridge schedule and lands it in the landing-zone of the
shared data lake bucket.

  - list_api_secrets()
        Enumerate Secrets Manager entries under the configured prefix
        (default: api-integration-agent/apis/) so the agent can browse known APIs.

  - read_api_secret(secret_name)
        Fetch one of the agent's API records. Returns ALL non-credential
        fields (endpoint, auth_type, dataset_hint, docs / schema, ...).
        The actual credential token is intentionally NOT returned to the
        agent — only a flag confirming it's present. The deployed Lambda
        reads the secret directly at runtime.

  - write_lambda_code(dataset, code, requirements?)
        Persist the agent-authored Python source to S3 under
        s3://${REPORT_BUCKET}/${CODE_PREFIX}<dataset>/lambda_function.py.
        Returns the codeUri.

  - deploy_lambda(dataset, code_uri, secret_name, schedule, env?)
        Package the code from S3, create/update the integration Lambda
        (function name api-integration-agent-ing-<dataset>) using the pre-created
        ${INGESTION_ROLE_ARN} role, configure environment, and create or
        update an EventBridge schedule rule that invokes it on the
        specified cron/rate expression.

  - invoke_lambda_test(dataset)
        Synchronously invoke the deployed Lambda once with a `{"test":
        true}` payload, then list the most-recent objects landed under
        landing/<dataset>/ to confirm data flowed end-to-end.

  - write_integration_report(dataset, report)
        Persists the agent's final integration report JSON to
        s3://${REPORT_BUCKET}/${REPORT_PREFIX}<dataset>.json.
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
import urllib.parse
import zipfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
S3 = boto3.client("s3", config=_CFG)
SECRETS = boto3.client("secretsmanager", config=_CFG)
LAMBDA = boto3.client("lambda", config=_CFG)
EVENTS = boto3.client("events", config=_CFG)

PREFIX = os.environ["PREFIX"]
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
LANDING_PREFIX = os.environ.get("LANDING_PREFIX", "landing/").rstrip("/") + "/"
CODE_PREFIX = os.environ.get("CODE_PREFIX", "api-integrations/code/").rstrip("/") + "/"
REPORT_PREFIX = os.environ.get("REPORT_PREFIX", "api-integrations/reports/").rstrip("/") + "/"
API_SECRET_PREFIX = os.environ.get("API_SECRET_PREFIX", "api-integration-agent/apis/")
INGESTION_ROLE_ARN = os.environ["INGESTION_ROLE_ARN"]
INGESTION_FN_PREFIX = os.environ.get("INGESTION_FN_PREFIX", "api-integration-agent-ing-")
INGESTION_RULE_PREFIX = os.environ.get("INGESTION_RULE_PREFIX", "api-integration-agent-ing-")
LAMBDA_RUNTIME = os.environ.get("INGESTION_LAMBDA_RUNTIME", "python3.12")
LAMBDA_TIMEOUT = int(os.environ.get("INGESTION_LAMBDA_TIMEOUT", "120"))
LAMBDA_MEMORY = int(os.environ.get("INGESTION_LAMBDA_MEMORY", "256"))


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in name.strip().lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-_") or "dataset"


def _fn_name(dataset: str) -> str:
    return f"{INGESTION_FN_PREFIX}{_slug(dataset)}"[:64]


def _rule_name(dataset: str) -> str:
    return f"{INGESTION_RULE_PREFIX}{_slug(dataset)}"[:64]


def tool_list_api_secrets(args: dict) -> dict:
    """List Secrets Manager entries under API_SECRET_PREFIX."""
    out = []
    paginator = SECRETS.get_paginator("list_secrets")
    for page in paginator.paginate(
        Filters=[{"Key": "name", "Values": [API_SECRET_PREFIX]}],
        MaxResults=100,
    ):
        for s in page.get("SecretList", []):
            name = s.get("Name", "")
            if not name.startswith(API_SECRET_PREFIX):
                continue
            out.append({
                "name": name,
                "description": s.get("Description"),
                "lastChanged": str(s.get("LastChangedDate")) if s.get("LastChangedDate") else None,
                "tags": {t["Key"]: t["Value"] for t in s.get("Tags", []) if "Key" in t},
            })
    return {"ok": True, "prefix": API_SECRET_PREFIX, "secrets": out, "count": len(out)}


def tool_read_api_secret(args: dict) -> dict:
    """Read an API secret. Strips raw credentials before returning."""
    secret_name = args["secret_name"]
    if not secret_name.startswith(API_SECRET_PREFIX):
        return {"ok": False, "error": f"secret {secret_name!r} not under prefix {API_SECRET_PREFIX!r}"}
    try:
        raw = SECRETS.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        return {"ok": False, "error": f"{e.response['Error'].get('Code')}: {e}"}
    body = raw.get("SecretString", "")
    try:
        parsed = json.loads(body)
    except Exception:
        return {"ok": False, "error": "secret payload is not valid JSON"}

    redact_keys = {"api_key", "apikey", "token", "secret", "password", "client_secret",
                   "bearer_token", "access_token", "private_key"}
    sanitized = {k: ("***REDACTED***" if k.lower() in redact_keys else v) for k, v in parsed.items()}
    has_credential = any(k.lower() in redact_keys for k in parsed.keys())
    return {
        "ok": True,
        "secretName": secret_name,
        "fields": sanitized,
        "hasCredential": has_credential,
        "credentialKey": next((k for k in parsed.keys() if k.lower() in redact_keys), None),
    }


def tool_write_lambda_code(args: dict) -> dict:
    """Persist agent-authored Lambda source code to S3."""
    dataset = _slug(args["dataset"])
    code = args["code"]
    requirements = args.get("requirements") or ""
    if not isinstance(code, str) or "def lambda_handler" not in code:
        return {"ok": False, "error": "code must be a Python string defining lambda_handler(event, context)"}
    code_key = f"{CODE_PREFIX}{dataset}/lambda_function.py"
    S3.put_object(
        Bucket=REPORT_BUCKET,
        Key=code_key,
        Body=code.encode("utf-8"),
        ContentType="text/x-python",
    )
    out = {"ok": True, "dataset": dataset, "codeUri": f"s3://{REPORT_BUCKET}/{code_key}"}
    if requirements.strip():
        req_key = f"{CODE_PREFIX}{dataset}/requirements.txt"
        S3.put_object(
            Bucket=REPORT_BUCKET,
            Key=req_key,
            Body=requirements.encode("utf-8"),
            ContentType="text/plain",
        )
        out["requirementsUri"] = f"s3://{REPORT_BUCKET}/{req_key}"
    return out


def _zip_bytes(code: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("lambda_function.py")
        zi.external_attr = 0o644 << 16
        zf.writestr(zi, code)
    return buf.getvalue()


def _ensure_lambda(fn_name: str, zip_bytes: bytes, env_vars: dict) -> str:
    try:
        LAMBDA.get_function(FunctionName=fn_name)
        LAMBDA.update_function_code(FunctionName=fn_name, ZipFile=zip_bytes, Publish=True)
        LAMBDA.get_waiter("function_updated").wait(FunctionName=fn_name)
        LAMBDA.update_function_configuration(
            FunctionName=fn_name,
            Role=INGESTION_ROLE_ARN,
            Runtime=LAMBDA_RUNTIME,
            Handler="lambda_function.lambda_handler",
            Timeout=LAMBDA_TIMEOUT,
            MemorySize=LAMBDA_MEMORY,
            Environment={"Variables": env_vars},
        )
        LAMBDA.get_waiter("function_updated").wait(FunctionName=fn_name)
    except LAMBDA.exceptions.ResourceNotFoundException:
        last_err = None
        for _ in range(8):
            try:
                LAMBDA.create_function(
                    FunctionName=fn_name,
                    Runtime=LAMBDA_RUNTIME,
                    Role=INGESTION_ROLE_ARN,
                    Handler="lambda_function.lambda_handler",
                    Code={"ZipFile": zip_bytes},
                    Timeout=LAMBDA_TIMEOUT,
                    MemorySize=LAMBDA_MEMORY,
                    Environment={"Variables": env_vars},
                    Publish=True,
                )
                last_err = None
                break
            except ClientError as e:
                msg = str(e)
                if "cannot be assumed" in msg or "InvalidParameterValueException" in msg:
                    last_err = e
                    time.sleep(5)
                    continue
                raise
        if last_err is not None:
            raise last_err
        LAMBDA.get_waiter("function_active").wait(FunctionName=fn_name)
    return LAMBDA.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]


def _ensure_schedule(rule_name: str, schedule: str, fn_arn: str, payload: dict) -> str:
    EVENTS.put_rule(
        Name=rule_name,
        ScheduleExpression=schedule,
        State="ENABLED",
        Description=f"api-integration-agent schedule for {rule_name}",
    )
    EVENTS.put_targets(
        Rule=rule_name,
        Targets=[{
            "Id": "1",
            "Arn": fn_arn,
            "Input": json.dumps(payload),
        }],
    )
    rule_arn = EVENTS.describe_rule(Name=rule_name)["Arn"]
    fn_name = fn_arn.split(":")[-1]
    try:
        LAMBDA.add_permission(
            FunctionName=fn_name,
            StatementId=f"eb-invoke-{rule_name}"[:100],
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except LAMBDA.exceptions.ResourceConflictException:
        pass
    return rule_arn


def tool_deploy_lambda(args: dict) -> dict:
    """Materialize a Lambda from the S3 code object + an EventBridge schedule."""
    dataset = _slug(args["dataset"])
    code_uri = args["code_uri"]
    secret_name = args.get("secret_name")
    schedule = args.get("schedule") or "rate(1 hour)"
    extra_env = args.get("env") or {}

    bucket, key = _parse_s3_uri(code_uri)
    if bucket != REPORT_BUCKET:
        return {"ok": False, "error": f"code_uri must live in {REPORT_BUCKET}"}
    obj = S3.get_object(Bucket=bucket, Key=key)
    code = obj["Body"].read().decode("utf-8")

    env_vars = {
        "DATASET": dataset,
        "DATA_BUCKET": REPORT_BUCKET,
        "LANDING_PREFIX": LANDING_PREFIX,
    }
    if secret_name:
        env_vars["API_SECRET_NAME"] = secret_name
    for k, v in extra_env.items():
        if isinstance(v, (str, int, float, bool)):
            env_vars[str(k)] = str(v)

    fn_name = _fn_name(dataset)
    fn_arn = _ensure_lambda(fn_name, _zip_bytes(code), env_vars)

    rule_name = _rule_name(dataset)
    rule_arn = _ensure_schedule(rule_name, schedule, fn_arn, {"trigger": "schedule", "dataset": dataset})

    return {
        "ok": True,
        "dataset": dataset,
        "functionName": fn_name,
        "functionArn": fn_arn,
        "ruleName": rule_name,
        "ruleArn": rule_arn,
        "schedule": schedule,
        "envKeys": sorted(env_vars.keys()),
    }


def tool_invoke_lambda_test(args: dict) -> dict:
    """Synchronously invoke the deployed Lambda and confirm an object landed."""
    dataset = _slug(args["dataset"])
    fn_name = _fn_name(dataset)
    payload = json.dumps({"trigger": "manual-test", "dataset": dataset}).encode("utf-8")
    try:
        resp = LAMBDA.invoke(
            FunctionName=fn_name,
            InvocationType="RequestResponse",
            LogType="Tail",
            Payload=payload,
        )
    except ClientError as e:
        return {"ok": False, "error": f"{e.response['Error'].get('Code')}: {e}"}

    raw = resp["Payload"].read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = raw
    function_error = resp.get("FunctionError")
    status = resp.get("StatusCode")

    landing_prefix = f"{LANDING_PREFIX}{dataset}/"
    listed = S3.list_objects_v2(Bucket=REPORT_BUCKET, Prefix=landing_prefix, MaxKeys=10)
    keys = [
        {
            "key": o["Key"],
            "size": o["Size"],
            "lastModified": str(o["LastModified"]),
        }
        for o in (listed.get("Contents") or [])
    ]
    keys.sort(key=lambda r: r["lastModified"], reverse=True)

    return {
        "ok": function_error is None and status and status < 400,
        "functionName": fn_name,
        "statusCode": status,
        "functionError": function_error,
        "responsePayload": parsed,
        "landingPrefix": f"s3://{REPORT_BUCKET}/{landing_prefix}",
        "objectsLanded": keys[:5],
        "objectCount": len(keys),
    }


def tool_write_integration_report(args: dict) -> dict:
    """Persist the agent's final integration report JSON."""
    dataset = _slug(args["dataset"])
    report = args["report"]
    key = f"{REPORT_PREFIX}{dataset}.json"
    body = report if isinstance(report, str) else json.dumps(report, indent=2, default=str)
    S3.put_object(
        Bucket=REPORT_BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    return {"ok": True, "reportUri": f"s3://{REPORT_BUCKET}/{key}"}


_TOOLS = {
    "list_api_secrets": tool_list_api_secrets,
    "read_api_secret": tool_read_api_secret,
    "write_lambda_code": tool_write_lambda_code,
    "deploy_lambda": tool_deploy_lambda,
    "invoke_lambda_test": tool_invoke_lambda_test,
    "write_integration_report": tool_write_integration_report,
}


def _resolve_tool(event, context) -> str | None:
    cc = getattr(context, "client_context", None)
    if cc is not None:
        custom = getattr(cc, "custom", None) or {}
        full = custom.get("bedrockAgentCoreToolName")
        if full:
            return full.split("___", 1)[-1]
    return event.get("toolName") or event.get("tool_name")


def lambda_handler(event, context):
    tool = _resolve_tool(event, context)
    args = {
        k: v
        for k, v in event.items()
        if not k.startswith("__") and k not in ("toolName", "tool_name")
    }
    logger.info("invoking tool=%s args_keys=%s", tool, list(args.keys()))
    handler = _TOOLS.get(tool)
    if handler is None:
        return {"error": f"unknown tool {tool!r}", "available": list(_TOOLS)}
    try:
        return handler(args)
    except ClientError as e:
        logger.exception("tool %s failed (ClientError)", tool)
        return {"error": f"{e.response['Error'].get('Code', 'ClientError')}: {e}", "tool": tool}
    except Exception as e:
        logger.exception("tool %s failed", tool)
        return {"error": f"{type(e).__name__}: {e}", "tool": tool}
