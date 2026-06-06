"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
data-architect-agent:

  - read_insights(insights_uri)
        Fetches the JSON written by the data-profiling-agent at
        s3://.../insights/<file>.json and returns it.

  - create_glue_database(database)
        Idempotently creates a Glue Catalog database.

  - create_glue_table(database, table, columns, location, partitions?, format?)
        Idempotently creates / replaces a Glue table. `columns` is a list of
        {name, type} dicts. `partitions` is an optional list of {name, type}
        dicts that will be removed from `columns` if present and registered
        as partition keys. `format` is one of csv | parquet (default parquet).

  - run_glue_job(job_name, role_arn, script_uri, args, timeout_minutes?)
        Idempotently creates / updates a Glue PySpark job and starts a run,
        polling until terminal. `args` is a dict of script arguments
        (e.g. {"--SOURCE_URI":"s3://...","--TARGET_URI":"s3://...",
        "--DATABASE":"db","--TABLE":"t","--PARTITION_COLS":"col1,col2"}).
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
S3 = boto3.client("s3", config=_CFG)
GLUE = boto3.client("glue", config=_CFG)

PREFIX = os.environ["PREFIX"]
DEFAULT_DATABASE = os.environ.get("DEFAULT_DATABASE", f"{PREFIX}_curated")

POLL_INTERVAL_SECONDS = 15
DEFAULT_TIMEOUT_MINUTES = 30
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def _safe_name(name: str) -> str:
    """Lower-case, alnum + underscore, length-limited Glue identifier."""
    n = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower() or "col"
    return n[:255]


def tool_read_insights(args: dict) -> dict:
    insights_uri = args["insights_uri"]
    bucket, key = _parse_s3_uri(insights_uri)
    body = S3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return {"ok": True, "insightsUri": insights_uri, "insights": json.loads(body)}


def tool_create_glue_database(args: dict) -> dict:
    database = args.get("database") or DEFAULT_DATABASE
    try:
        GLUE.create_database(DatabaseInput={"Name": database})
        logger.info("database %s created", database)
        return {"ok": True, "database": database, "created": True}
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            return {"ok": True, "database": database, "created": False}
        raise


def _table_input(
    table: str,
    columns: list[dict],
    location: str,
    partitions: list[dict] | None,
    fmt: str,
) -> dict:
    fmt = (fmt or "parquet").lower()
    if fmt == "parquet":
        ser_lib = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
        input_fmt = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
        output_fmt = (
            "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
        )
        params = {"classification": "parquet", "EXTERNAL": "TRUE"}
    elif fmt == "csv":
        ser_lib = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
        input_fmt = "org.apache.hadoop.mapred.TextInputFormat"
        output_fmt = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
        params = {
            "classification": "csv",
            "skip.header.line.count": "1",
            "field.delim": ",",
            "EXTERNAL": "TRUE",
        }
    else:
        raise ValueError(f"unsupported format {fmt!r}")

    cols = [
        {"Name": _safe_name(c["name"]), "Type": (c.get("type") or "string").lower()}
        for c in columns
    ]
    parts = [
        {"Name": _safe_name(p["name"]), "Type": (p.get("type") or "string").lower()}
        for p in (partitions or [])
    ]
    if not location.endswith("/"):
        location = location + "/"
    return {
        "Name": table,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": params,
        "PartitionKeys": parts,
        "StorageDescriptor": {
            "Columns": cols,
            "Location": location,
            "InputFormat": input_fmt,
            "OutputFormat": output_fmt,
            "Compressed": fmt == "parquet",
            "SerdeInfo": {
                "SerializationLibrary": ser_lib,
                "Parameters": {"serialization.format": "1"}
                if fmt == "parquet"
                else {"field.delim": ","},
            },
        },
    }


def tool_create_glue_table(args: dict) -> dict:
    database = args.get("database") or DEFAULT_DATABASE
    table = _safe_name(args["table"])
    columns = args["columns"]
    location = args["location"]
    partitions = args.get("partitions") or []
    fmt = (args.get("format") or "parquet").lower()

    # Drop partition columns from main column list if duplicated
    part_names = {_safe_name(p["name"]) for p in partitions}
    columns = [c for c in columns if _safe_name(c["name"]) not in part_names]

    ti = _table_input(table, columns, location, partitions, fmt)
    try:
        GLUE.create_table(DatabaseName=database, TableInput=ti)
        logger.info("table %s.%s created", database, table)
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        GLUE.update_table(DatabaseName=database, TableInput=ti)
        logger.info("table %s.%s updated", database, table)
        created = False
    return {
        "ok": True,
        "database": database,
        "table": table,
        "created": created,
        "location": location,
        "format": fmt,
        "columns": [{"name": c["Name"], "type": c["Type"]} for c in ti["StorageDescriptor"]["Columns"]],
        "partitionKeys": [{"name": p["Name"], "type": p["Type"]} for p in ti["PartitionKeys"]],
    }


def _wait_glue_run(job_name: str, run_id: str, timeout_min: int) -> dict:
    deadline = time.time() + timeout_min * 60
    last: dict = {}
    while time.time() < deadline:
        last = GLUE.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        state = last.get("JobRunState", "")
        logger.info("job=%s run=%s state=%s", job_name, run_id, state)
        if state in TERMINAL_STATES:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"glue job {job_name} run {run_id} did not finish in {timeout_min}m; "
        f"last state={last.get('JobRunState') or 'unknown'}"
    )


def tool_run_glue_job(args: dict) -> dict:
    job_name = args["job_name"]
    role_arn = args["role_arn"]
    script_uri = args["script_uri"]
    job_args = args.get("args") or {}
    timeout_min = int(args.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES))

    # Default arguments include enable-metrics off and a temp dir
    bucket, _ = _parse_s3_uri(script_uri)
    default_args = {
        "--TempDir": f"s3://{bucket}/glue-temp/",
        "--job-language": "python",
        "--enable-continuous-cloudwatch-log": "true",
        "--enable-metrics": "false",
        "--enable-glue-datacatalog": "true",
    }
    default_args.update(job_args)

    job_input = {
        "Name": job_name,
        "Role": role_arn,
        "Command": {
            "Name": "glueetl",
            "ScriptLocation": script_uri,
            "PythonVersion": "3",
        },
        "DefaultArguments": default_args,
        "GlueVersion": "4.0",
        "WorkerType": "G.1X",
        "NumberOfWorkers": 2,
        "Timeout": timeout_min,
        "ExecutionProperty": {"MaxConcurrentRuns": 1},
    }
    try:
        GLUE.create_job(**job_input)
        logger.info("glue job %s created", job_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        update_body = {k: v for k, v in job_input.items() if k != "Name"}
        GLUE.update_job(JobName=job_name, JobUpdate=update_body)
        logger.info("glue job %s updated", job_name)

    run_id = GLUE.start_job_run(JobName=job_name)["JobRunId"]
    logger.info("started glue job %s run=%s", job_name, run_id)

    final = _wait_glue_run(job_name, run_id, timeout_min)
    state = final.get("JobRunState")
    return {
        "ok": state == "SUCCEEDED",
        "job": job_name,
        "runId": run_id,
        "state": state,
        "error": final.get("ErrorMessage"),
        "logGroupName": final.get("LogGroupName"),
        "args": job_args,
    }


_TOOLS = {
    "read_insights": tool_read_insights,
    "create_glue_database": tool_create_glue_database,
    "create_glue_table": tool_create_glue_table,
    "run_glue_job": tool_run_glue_job,
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
