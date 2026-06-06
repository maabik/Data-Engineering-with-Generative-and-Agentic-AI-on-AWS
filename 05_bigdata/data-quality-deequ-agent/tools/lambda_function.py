"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
data-quality-deequ-agent.

  - read_design(design_uri)
        Fetches the JSON written by the data-architect-agent at
        s3://${BUCKET}/designs/<file>.json.

  - run_deequ_job(database, table, run_id, timeout_minutes?)
        Starts the persistent data-quality-deequ-agent-deequ-job Glue job, polling until
        terminal. The job writes suggestions/, results/, parquet/ under
        s3://${REPORT_BUCKET}/${REPORT_PREFIX}<table>/.

  - register_report_table(database, table)
        Idempotently creates the dq_results Glue Catalog table over the
        Parquet output so Athena can query it.

  - query_report(query, output_location?)
        Runs an Athena query against the data-quality-deequ-agent workgroup; returns rows.

  - derive_quality_insights(run_id, database, table)
        Reads the JSON results file and returns a slim summary
        (passed, failed, warnings, top failing checks) — small enough
        to drop directly into the agent's final reasoning step.
"""
from __future__ import annotations

import json
import logging
import os
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
ATHENA = boto3.client("athena", config=_CFG)

PREFIX = os.environ["PREFIX"]
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
REPORT_PREFIX = os.environ.get("REPORT_PREFIX", "dq-reports/").rstrip("/") + "/"
DEEQU_JOB_NAME = os.environ.get("DEEQU_JOB_NAME", f"{PREFIX}-deequ-job")
DQ_DATABASE = os.environ.get("DQ_DATABASE", f"{PREFIX}_dq")
DQ_TABLE = os.environ.get("DQ_TABLE", "dq_results")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", f"{PREFIX}-wg")
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION")

POLL = 15
DEFAULT_TIMEOUT_MIN = 30
TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"expected s3:// uri, got {s3_uri!r}")
    rest = s3_uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"malformed s3 uri: {s3_uri!r}")
    return bucket, urllib.parse.unquote_plus(key)


def tool_read_design(args: dict) -> dict:
    design_uri = args["design_uri"]
    bucket, key = _parse_s3_uri(design_uri)
    body = S3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return {"ok": True, "designUri": design_uri, "design": json.loads(body)}


def _wait_glue_run(job_name: str, run_id: str, timeout_min: int) -> dict:
    deadline = time.time() + timeout_min * 60
    last: dict = {}
    while time.time() < deadline:
        last = GLUE.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        state = last.get("JobRunState", "")
        logger.info("job=%s run=%s state=%s", job_name, run_id, state)
        if state in TERMINAL:
            return last
        time.sleep(POLL)
    raise TimeoutError(
        f"deequ job {job_name} run {run_id} did not finish in {timeout_min}m; "
        f"last state={last.get('JobRunState') or 'unknown'}"
    )


def tool_run_deequ_job(args: dict) -> dict:
    database = args["database"]
    table = args["table"]
    run_id = args["run_id"]
    timeout_min = int(args.get("timeout_minutes", DEFAULT_TIMEOUT_MIN))
    min_null_fraction = str(args.get("min_null_fraction", 0.99))

    job_args = {
        "--DATABASE": database,
        "--TABLE": table,
        "--REPORT_BUCKET": REPORT_BUCKET,
        "--REPORT_PREFIX": f"{REPORT_PREFIX}{table}/",
        "--RUN_ID": run_id,
        "--MIN_NULL_FRACTION": min_null_fraction,
    }
    response = GLUE.start_job_run(JobName=DEEQU_JOB_NAME, Arguments=job_args)
    run_jr_id = response["JobRunId"]
    logger.info("started deequ job run=%s", run_jr_id)

    final = _wait_glue_run(DEEQU_JOB_NAME, run_jr_id, timeout_min)
    state = final.get("JobRunState")
    return {
        "ok": state == "SUCCEEDED",
        "job": DEEQU_JOB_NAME,
        "runJobRunId": run_jr_id,
        "runId": run_id,
        "state": state,
        "error": final.get("ErrorMessage"),
        "args": job_args,
        "suggestionsUri": f"s3://{REPORT_BUCKET}/{REPORT_PREFIX}{table}/suggestions/{run_id}.json",
        "resultsUri": f"s3://{REPORT_BUCKET}/{REPORT_PREFIX}{table}/results/{run_id}.json",
        "parquetPrefix": f"s3://{REPORT_BUCKET}/{REPORT_PREFIX}{table}/parquet/",
    }


_DQ_RESULT_COLUMNS = [
    {"Name": "check", "Type": "string"},
    {"Name": "check_level", "Type": "string"},
    {"Name": "check_status", "Type": "string"},
    {"Name": "constraint", "Type": "string"},
    {"Name": "constraint_status", "Type": "string"},
    {"Name": "constraint_message", "Type": "string"},
    {"Name": "source_database", "Type": "string"},
    {"Name": "source_table", "Type": "string"},
    {"Name": "ts", "Type": "string"},
]


def tool_register_report_table(args: dict) -> dict:
    database = args.get("database") or DQ_DATABASE
    table = args.get("table") or DQ_TABLE

    try:
        GLUE.create_database(DatabaseInput={"Name": database})
        logger.info("database %s created", database)
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise

    location = f"s3://{REPORT_BUCKET}/{REPORT_PREFIX}{args['source_table']}/parquet/"
    table_input = {
        "Name": table,
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {
            "classification": "parquet",
            "EXTERNAL": "TRUE",
            "projection.enabled": "true",
            "projection.run_id.type": "injected",
            "storage.location.template": f"{location}run_id=${{run_id}}/",
        },
        "PartitionKeys": [{"Name": "run_id", "Type": "string"}],
        "StorageDescriptor": {
            "Columns": _DQ_RESULT_COLUMNS,
            "Location": location,
            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
            "Compressed": True,
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                "Parameters": {"serialization.format": "1"},
            },
        },
    }
    try:
        GLUE.create_table(DatabaseName=database, TableInput=table_input)
        created = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        GLUE.update_table(DatabaseName=database, TableInput=table_input)
        created = False
    return {
        "ok": True,
        "database": database,
        "table": table,
        "location": location,
        "created": created,
    }


def tool_query_report(args: dict) -> dict:
    query = args["query"]
    output = args.get("output_location") or ATHENA_OUTPUT_LOCATION
    if not output:
        return {"ok": False, "error": "no Athena output_location configured"}

    qx = ATHENA.start_query_execution(
        QueryString=query,
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": output},
    )
    qid = qx["QueryExecutionId"]
    deadline = time.time() + 120
    while time.time() < deadline:
        s = ATHENA.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if s["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    if s["State"] != "SUCCEEDED":
        return {
            "ok": False,
            "queryExecutionId": qid,
            "state": s["State"],
            "error": s.get("StateChangeReason"),
        }

    rs = ATHENA.get_query_results(QueryExecutionId=qid, MaxResults=200)
    cols = [
        c["Name"] for c in rs["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
    ]
    rows = []
    data_rows = rs["ResultSet"]["Rows"][1:]  # skip header
    for r in data_rows:
        rows.append(
            {cols[i]: (d.get("VarCharValue") if d else None) for i, d in enumerate(r["Data"])}
        )
    return {
        "ok": True,
        "queryExecutionId": qid,
        "columns": cols,
        "rows": rows,
        "rowCount": len(rows),
    }


def tool_derive_quality_insights(args: dict) -> dict:
    run_id = args["run_id"]
    table = args["table"]
    results_key = f"{REPORT_PREFIX}{table}/results/{run_id}.json"
    obj = S3.get_object(Bucket=REPORT_BUCKET, Key=results_key)
    payload = json.loads(obj["Body"].read())
    checks = payload.get("checks", [])
    failed = [c for c in checks if (c.get("constraint_status") or "").lower() != "success"]
    passed = [c for c in checks if (c.get("constraint_status") or "").lower() == "success"]
    return {
        "ok": True,
        "runId": run_id,
        "database": payload.get("database"),
        "table": payload.get("table"),
        "overallStatus": payload.get("overallStatus"),
        "totalChecks": len(checks),
        "passedCount": len(passed),
        "failedCount": len(failed),
        "topFailing": failed[:10],
    }


_TOOLS = {
    "read_design": tool_read_design,
    "run_deequ_job": tool_run_deequ_job,
    "register_report_table": tool_register_report_table,
    "query_report": tool_query_report,
    "derive_quality_insights": tool_derive_quality_insights,
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
