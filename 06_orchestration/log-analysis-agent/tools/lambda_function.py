"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
log-analysis-agent.

The agent receives the URI of a pipeline-run JSON
(s3://${BUCKET}/pipeline-runs/<run_id>.json) — written by an upstream
orchestrator (Airflow, Step Functions, EMR step) when a pipeline
execution finishes — and turns it into a structured analysis of what
the Airflow and Spark logs say happened, persists that analysis as
JSON, and publishes an SNS notification.

  - read_pipeline_run(run_uri)
        Slim run descriptor: {run_id, pipeline, status, started_at,
        ended_at, airflow:{...}, spark:{...}}.

  - read_airflow_logs(source, max_lines?)
        Pull Airflow task logs from either S3 (`{"s3_uri":...}`) or
        CloudWatch Logs (`{"log_group":..., "log_stream":...}`).
        Returns the most recent N lines.

  - read_spark_logs(source, max_lines?)
        Same shape as read_airflow_logs but for the Spark driver/
        executor stderr (or its CloudWatch equivalent on EMR-on-EKS).

  - list_pipeline_history(pipeline, limit?)
        S3 listing of recent pipeline-runs/<pipeline>* records — used
        to detect chronic vs first-time failures without an Athena
        round-trip.

  - write_log_insights(run_uri, insights)
        Persists the agent's final JSON to
        s3://${REPORT_BUCKET}/${INSIGHTS_PREFIX}<run_id>.json.

  - send_sns_notification(subject, message)
        Publishes the agent's headline summary to the configured
        ${SNS_TOPIC_ARN}.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import os
import urllib.parse
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
S3 = boto3.client("s3", config=_CFG)
LOGS = boto3.client("logs", config=_CFG)
SNS = boto3.client("sns", config=_CFG)

PREFIX = os.environ["PREFIX"]
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
RUN_PREFIX = os.environ.get("RUN_PREFIX", "pipeline-runs/").rstrip("/") + "/"
INSIGHTS_PREFIX = os.environ.get("INSIGHTS_PREFIX", "log-insights/").rstrip("/") + "/"
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

LOG_LINE_DEFAULT = 200
LOG_LINE_MAX = 1000
HISTORY_DEFAULT = 10
HISTORY_MAX = 50


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def _read_s3_json(s3_uri: str) -> dict:
    bucket, key = _parse_s3_uri(s3_uri)
    obj = S3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())


def _read_s3_text(s3_uri: str, max_lines: int) -> list[str]:
    """Read text lines from s3, transparently un-gzipping .gz objects.

    For prefixes ending in `/`, concatenate every object's content in
    sorted-key order — matches the EMR Serverless `applications/.../
    SPARK_DRIVER/stderr.gz` layout where multiple log parts live under
    one prefix.
    """
    bucket, key = _parse_s3_uri(s3_uri)
    all_lines: list[str] = []
    if key.endswith("/") or not key:
        paginator = S3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            for c in page.get("Contents") or []:
                keys.append(c["Key"])
        keys.sort()
        for k in keys:
            all_lines.extend(_read_one_object(bucket, k))
            if len(all_lines) >= max_lines * 4:
                break
    else:
        all_lines.extend(_read_one_object(bucket, key))
    return all_lines[-max_lines:]


def _read_one_object(bucket: str, key: str) -> list[str]:
    obj = S3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    if key.endswith(".gz"):
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    return [ln for ln in text.splitlines() if ln.strip()]


def _read_cwl(log_group: str, log_stream: str | None, max_lines: int) -> list[str]:
    if log_stream:
        events: list[dict] = []
        token = None
        while len(events) < max_lines:
            kwargs = dict(
                logGroupName=log_group,
                logStreamName=log_stream,
                limit=min(max_lines - len(events), 1000),
                startFromHead=False,
            )
            if token:
                kwargs["nextToken"] = token
            resp = LOGS.get_log_events(**kwargs)
            evts = resp.get("events") or []
            if not evts:
                break
            events.extend(evts)
            new_token = resp.get("nextBackwardToken")
            if new_token == token:
                break
            token = new_token
        events.sort(key=lambda e: e.get("timestamp") or 0)
        return [e.get("message", "") for e in events][-max_lines:]
    paginator = LOGS.get_paginator("filter_log_events")
    out: list[str] = []
    for page in paginator.paginate(logGroupName=log_group, limit=min(max_lines, 1000)):
        for e in page.get("events") or []:
            out.append(e.get("message", ""))
            if len(out) >= max_lines:
                break
        if len(out) >= max_lines:
            break
    return out[-max_lines:]


def _fetch_logs(source: dict, max_lines: int) -> dict:
    if not isinstance(source, dict):
        return {"ok": False, "error": "source must be an object"}
    if "s3_uri" in source and source["s3_uri"]:
        try:
            lines = _read_s3_text(source["s3_uri"], max_lines)
        except ClientError as e:
            return {"ok": False, "error": f"s3 read failed: {e}", "source": source}
        return {
            "ok": True,
            "source": {"type": "s3", "s3_uri": source["s3_uri"]},
            "lineCount": len(lines),
            "logs": "\n".join(lines),
        }
    if "log_group" in source and source["log_group"]:
        try:
            lines = _read_cwl(source["log_group"], source.get("log_stream"), max_lines)
        except ClientError as e:
            return {"ok": False, "error": f"cloudwatch read failed: {e}", "source": source}
        return {
            "ok": True,
            "source": {
                "type": "cloudwatch",
                "log_group": source["log_group"],
                "log_stream": source.get("log_stream"),
            },
            "lineCount": len(lines),
            "logs": "\n".join(lines),
        }
    return {"ok": False, "error": "source must contain s3_uri or log_group"}


def tool_read_pipeline_run(args: dict) -> dict:
    run_uri = args["run_uri"]
    return {"ok": True, "runUri": run_uri, "run": _read_s3_json(run_uri)}


def tool_read_airflow_logs(args: dict) -> dict:
    source = args.get("source") or {}
    max_lines = min(int(args.get("max_lines", LOG_LINE_DEFAULT)), LOG_LINE_MAX)
    res = _fetch_logs(source, max_lines)
    res["component"] = "airflow"
    return res


def tool_read_spark_logs(args: dict) -> dict:
    source = args.get("source") or {}
    max_lines = min(int(args.get("max_lines", LOG_LINE_DEFAULT)), LOG_LINE_MAX)
    res = _fetch_logs(source, max_lines)
    res["component"] = "spark"
    return res


def tool_list_pipeline_history(args: dict) -> dict:
    pipeline = args["pipeline"]
    limit = min(int(args.get("limit", HISTORY_DEFAULT)), HISTORY_MAX)
    paginator = S3.get_paginator("list_objects_v2")
    matched: list[dict] = []
    for page in paginator.paginate(Bucket=REPORT_BUCKET, Prefix=RUN_PREFIX):
        for c in page.get("Contents") or []:
            base = os.path.basename(c["Key"])
            if not base.endswith(".json"):
                continue
            if pipeline and pipeline not in base:
                continue
            matched.append(
                {
                    "key": c["Key"],
                    "lastModified": c["LastModified"].isoformat(),
                    "size": c["Size"],
                }
            )
    matched.sort(key=lambda r: r["lastModified"], reverse=True)
    return {"ok": True, "pipeline": pipeline, "rowCount": len(matched[:limit]), "rows": matched[:limit]}


def tool_write_log_insights(args: dict) -> dict:
    run_uri = args["run_uri"]
    insights = args["insights"]
    base = os.path.basename(run_uri).rsplit(".", 1)[0] or "run"
    key = f"{INSIGHTS_PREFIX}{base}.json"
    body = insights if isinstance(insights, str) else json.dumps(insights, indent=2, default=str)
    S3.put_object(
        Bucket=REPORT_BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    return {"ok": True, "insightsUri": f"s3://{REPORT_BUCKET}/{key}"}


def tool_send_sns_notification(args: dict) -> dict:
    if not SNS_TOPIC_ARN:
        return {"ok": False, "error": "SNS_TOPIC_ARN not configured"}
    subject = (args.get("subject") or "log-analysis-agent")[:100]
    message = args.get("message") or ""
    if isinstance(message, (dict, list)):
        message = json.dumps(message, indent=2, default=str)
    resp = SNS.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=str(message))
    return {"ok": True, "messageId": resp.get("MessageId"), "topicArn": SNS_TOPIC_ARN}


_TOOLS = {
    "read_pipeline_run": tool_read_pipeline_run,
    "read_airflow_logs": tool_read_airflow_logs,
    "read_spark_logs": tool_read_spark_logs,
    "list_pipeline_history": tool_list_pipeline_history,
    "write_log_insights": tool_write_log_insights,
    "send_sns_notification": tool_send_sns_notification,
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
