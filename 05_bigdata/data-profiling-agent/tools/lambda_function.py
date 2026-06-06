"""Lambda target for AgentCore Gateway. Hosts two MCP tools:

  - profile_dataset(s3_uri[, timeout_seconds])
        creates/updates a Glue DataBrew dataset + FULL_DATASET profile job
        for the new S3 object, starts an asynchronous run, polls until
        terminal, and returns the job + report S3 URI.

  - analyze_profiling_report(report_uri)
        fetches the (potentially 8k-line) DataBrew profile JSON, drops bulky
        per-column histograms, and returns a model-friendly summary so the
        agent can reason about schema, primary keys, partitioning candidates
        and PII without blowing its context window.

Gateway invokes this Lambda once per tool call. The tool name is provided
either in `event['__context__']['toolName']` or as
`context.client_context.custom['bedrockAgentCoreToolName']` (formatted as
`<targetName>___<toolName>`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
DATABREW = boto3.client("databrew", config=_CFG)
S3 = boto3.client("s3", config=_CFG)

PREFIX = os.environ["PREFIX"]
BUCKET = os.environ["BUCKET"]
PROFILE_PREFIX = os.environ.get("PROFILE_PREFIX", "profile/").rstrip("/") + "/"
DATABREW_ROLE_ARN = os.environ["DATABREW_ROLE_ARN"]

POLL_INTERVAL_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 720
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT"}

COLUMN_STATISTICS = [
    "DISTINCT_VALUES_COUNT",
    "UNIQUE_VALUES_COUNT",
    "VALUE_DISTRIBUTION",
    "PERCENTILES",
    "RANGE",
    "SKEWNESS",
    "STANDARD_DEVIATION",
    "VARIANCE",
    "MEAN",
    "MEDIAN",
    "MODE",
    "MIN",
    "MAX",
    "MISSING_VALUES_COUNT",
]


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def _resource_name(s3_key: str) -> str:
    base = os.path.splitext(os.path.basename(s3_key))[0]
    safe = re.sub(r"[^a-zA-Z0-9-]+", "-", base).strip("-").lower() or "dataset"
    return f"{PREFIX}-{safe}"[:240]


def _detect_format(key: str) -> dict:
    lower = key.lower()
    if lower.endswith(".json"):
        return {"Format": "JSON", "FormatOptions": {"Json": {"MultiLine": False}}}
    if lower.endswith(".parquet"):
        return {"Format": "PARQUET"}
    return {"Format": "CSV", "FormatOptions": {"Csv": {"Delimiter": ","}}}


def _upsert_dataset(name: str, bucket: str, key: str) -> None:
    body = {
        "Name": name,
        **_detect_format(key),
        "Input": {"S3InputDefinition": {"Bucket": bucket, "Key": key}},
    }
    try:
        DATABREW.create_dataset(**body)
        logger.info("dataset %s created", name)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    DATABREW.update_dataset(**body)
    logger.info("dataset %s updated", name)


def _upsert_profile_job(name: str, dataset_name: str, output_prefix: str) -> None:
    body = {
        "Name": name,
        "DatasetName": dataset_name,
        "RoleArn": DATABREW_ROLE_ARN,
        "JobSample": {"Mode": "FULL_DATASET"},
        "OutputLocation": {"Bucket": BUCKET, "Key": output_prefix},
        "Configuration": {
            "ColumnStatisticsConfigurations": [
                {"Statistics": {"IncludedStatistics": COLUMN_STATISTICS}}
            ],
            "DatasetStatisticsConfiguration": {"IncludedStatistics": ["NONE"]},
        },
    }
    try:
        DATABREW.create_profile_job(**body)
        logger.info("profile job %s created", name)
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            raise
    update_body = {k: v for k, v in body.items() if k != "DatasetName"}
    DATABREW.update_profile_job(**update_body)
    logger.info("profile job %s updated", name)


def _wait_for_run(job_name: str, run_id: str, timeout_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        last = DATABREW.describe_job_run(Name=job_name, RunId=run_id)
        state = last.get("State", "")
        logger.info("job=%s run=%s state=%s", job_name, run_id, state)
        if state in TERMINAL_STATES:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"profile job {job_name} run {run_id} did not finish in {timeout_seconds}s; "
        f"last state={last.get('State') or 'unknown'}"
    )


def _slim_report(report: dict) -> dict:
    """Drop bulky histogram payloads; keep header + per-column summary stats.

    DataBrew profile reports can be 8k+ lines of JSON dominated by per-column
    valueDistribution histograms. The agent's analysis only needs aggregate
    stats per column, so we keep dataset header + per-column summary.
    """
    slim: dict[str, Any] = {
        "sampleSize": report.get("sampleSize"),
        "duplicateRowsCount": report.get("duplicateRowsCount"),
        "totalMissingValuesCount": report.get("totalMissingValuesCount"),
    }
    columns: list[dict] = []
    for col in report.get("columns", []) or []:
        c = {k: v for k, v in col.items() if k != "valueDistribution"}
        columns.append(c)
    slim["columns"] = columns
    return slim


def _find_report(output_prefix: str) -> tuple[str, dict]:
    listing = S3.list_objects_v2(Bucket=BUCKET, Prefix=output_prefix)
    contents = sorted(
        listing.get("Contents") or [], key=lambda o: o["LastModified"], reverse=True
    )
    for obj in contents:
        if obj["Key"].endswith(".json"):
            body = S3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
            return f"s3://{BUCKET}/{obj['Key']}", json.loads(body)
    raise FileNotFoundError(
        f"no profile JSON found under s3://{BUCKET}/{output_prefix}"
    )


def tool_profile_dataset(args: dict) -> dict:
    s3_uri = args["s3_uri"]
    timeout = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    bucket, key = _parse_s3_uri(s3_uri)
    name = _resource_name(key)
    output_prefix = f"{PROFILE_PREFIX}{name}/"

    _upsert_dataset(name, bucket, key)
    _upsert_profile_job(name, name, output_prefix)

    run = DATABREW.start_job_run(Name=name)
    run_id = run["RunId"]
    logger.info("started profile job %s run=%s", name, run_id)

    final = _wait_for_run(name, run_id, timeout)
    state = final.get("State")
    if state != "SUCCEEDED":
        return {
            "ok": False,
            "job": name,
            "runId": run_id,
            "state": state,
            "error": final.get("ErrorMessage"),
        }

    report_uri, _ = _find_report(output_prefix)
    return {
        "ok": True,
        "job": name,
        "runId": run_id,
        "state": state,
        "input": s3_uri,
        "reportUri": report_uri,
    }


def tool_analyze_profiling_report(args: dict) -> dict:
    report_uri = args["report_uri"]
    bucket, key = _parse_s3_uri(report_uri)
    body = S3.get_object(Bucket=bucket, Key=key)["Body"].read()
    report = json.loads(body)
    return {"ok": True, "reportUri": report_uri, "report": _slim_report(report)}


_TOOLS = {
    "profile_dataset": tool_profile_dataset,
    "analyze_profiling_report": tool_analyze_profiling_report,
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
    except Exception as e:
        logger.exception("tool %s failed", tool)
        return {"error": f"{type(e).__name__}: {e}", "tool": tool}
