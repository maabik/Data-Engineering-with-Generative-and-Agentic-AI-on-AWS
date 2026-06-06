"""Lambda target for AgentCore Gateway. Hosts MCP tools for the
data-quality-agent (Deequ-report remediation analyst).

The agent receives the URI of an insights JSON written by
data-quality-deequ-agent (s3://${BUCKET}/dq-insights/<file>.json) and
turns it into an actionable remediation plan. These tools cover the
read-side of that work; the agent writes the final plan via
write_remediation_plan.

  - read_dq_insights(insights_uri)
        Slim summary the data-quality-deequ-agent already produced.

  - read_dq_results(results_uri)
        Full Deequ results JSON for a single run (per-constraint rows
        + completeness map).

  - read_design(design_uri)
        Original Data Architect design (column types, partition keys,
        table location). Used to translate Deequ check names back into
        the column / business-meaning the data engineer cares about.

  - query_dq_history(table, limit?)
        Athena query against {DQ_DATABASE}.{DQ_TABLE} that returns the
        last N runs for a source table — pass-rate trend, drift
        signals, fresh-vs-recurring failures.

  - peek_failing_rows(database, table, predicate, limit?)
        Athena `SELECT *` against the curated table with a caller-
        supplied WHERE predicate (e.g. `fine IS NULL OR fine < 0`).
        Used to confirm the actual records behind a failed Deequ
        constraint.

  - write_remediation_plan(insights_uri, plan)
        Persists the agent's final JSON plan to
        s3://${REPORT_BUCKET}/${PLAN_PREFIX}<basename>.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
S3 = boto3.client("s3", config=_CFG)
ATHENA = boto3.client("athena", config=_CFG)

PREFIX = os.environ["PREFIX"]
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
PLAN_PREFIX = os.environ.get("PLAN_PREFIX", "dq-remediation/").rstrip("/") + "/"
DQ_DATABASE = os.environ.get("DQ_DATABASE", "data_quality_deequ_agent_dq")
DQ_TABLE = os.environ.get("DQ_TABLE", "dq_results")
DQ_REPORTS_PREFIX = os.environ.get("DQ_REPORTS_PREFIX", "dq-reports/").rstrip("/") + "/"
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", f"{PREFIX}-wg")
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION")

ATHENA_TIMEOUT_S = 120
PEEK_DEFAULT = 10
PEEK_MAX = 50
HISTORY_DEFAULT = 20
HISTORY_MAX = 100


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


def tool_read_dq_insights(args: dict) -> dict:
    insights_uri = args["insights_uri"]
    return {"ok": True, "insightsUri": insights_uri, "insights": _read_s3_json(insights_uri)}


def tool_read_dq_results(args: dict) -> dict:
    results_uri = args["results_uri"]
    return {"ok": True, "resultsUri": results_uri, "results": _read_s3_json(results_uri)}


def tool_read_design(args: dict) -> dict:
    design_uri = args["design_uri"]
    return {"ok": True, "designUri": design_uri, "design": _read_s3_json(design_uri)}


def _run_athena(query: str) -> dict:
    if not ATHENA_OUTPUT_LOCATION:
        return {"ok": False, "error": "no Athena output_location configured"}
    qx = ATHENA.start_query_execution(
        QueryString=query,
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_LOCATION},
    )
    qid = qx["QueryExecutionId"]
    deadline = time.time() + ATHENA_TIMEOUT_S
    state = "RUNNING"
    reason = None
    while time.time() < deadline:
        s = ATHENA.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = s["State"]
        reason = s.get("StateChangeReason")
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    if state != "SUCCEEDED":
        return {"ok": False, "queryExecutionId": qid, "state": state, "error": reason}
    rs = ATHENA.get_query_results(QueryExecutionId=qid, MaxResults=200)
    cols = [c["Name"] for c in rs["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    rows = []
    for r in rs["ResultSet"]["Rows"][1:]:
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


def _list_run_ids(table: str) -> list[str]:
    """Enumerate run_id partitions from S3.

    The dq_results table uses Athena partition projection of type "injected",
    which requires a static equality filter on run_id. We list the parquet
    layout under dq-reports/<table>/parquet/run_id=<id>/ to seed an IN (...)
    clause for the history query.
    """
    prefix = f"{DQ_REPORTS_PREFIX}{table}/parquet/"
    out: list[str] = []
    paginator = S3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=REPORT_BUCKET, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes") or []:
            seg = cp["Prefix"][len(prefix):].strip("/")
            if seg.startswith("run_id="):
                out.append(seg.split("=", 1)[1])
    return out


def tool_query_dq_history(args: dict) -> dict:
    table = args["table"]
    limit = min(int(args.get("limit", HISTORY_DEFAULT)), HISTORY_MAX)
    run_ids = _list_run_ids(table)
    if not run_ids:
        return {"ok": True, "columns": ["run_id", "total", "passed", "failed", "last_ts"],
                "rows": [], "rowCount": 0, "note": f"no partitions under {DQ_REPORTS_PREFIX}{table}/"}
    in_list = ", ".join(f"'{r}'" for r in run_ids)
    sql = (
        f"WITH t AS ("
        f"  SELECT run_id,"
        f"         COUNT(*) AS total,"
        f"         SUM(CASE WHEN constraint_status = 'Success' THEN 1 ELSE 0 END) AS passed,"
        f"         SUM(CASE WHEN constraint_status <> 'Success' THEN 1 ELSE 0 END) AS failed,"
        f"         MAX(ts) AS last_ts"
        f"  FROM \"{DQ_DATABASE}\".\"{DQ_TABLE}\""
        f"  WHERE run_id IN ({in_list})"
        f"    AND source_table = '{table}'"
        f"  GROUP BY run_id"
        f") SELECT run_id, total, passed, failed, last_ts "
        f"FROM t ORDER BY last_ts DESC LIMIT {limit}"
    )
    return _run_athena(sql)


def tool_peek_failing_rows(args: dict) -> dict:
    database = args["database"]
    table = args["table"]
    predicate = args["predicate"]
    limit = min(int(args.get("limit", PEEK_DEFAULT)), PEEK_MAX)
    if ";" in predicate or "--" in predicate or "/*" in predicate:
        return {"ok": False, "error": "predicate contains forbidden tokens"}
    sql = (
        f"SELECT * FROM \"{database}\".\"{table}\" "
        f"WHERE {predicate} LIMIT {limit}"
    )
    return _run_athena(sql)


def tool_write_remediation_plan(args: dict) -> dict:
    insights_uri = args["insights_uri"]
    plan = args["plan"]
    base = os.path.basename(insights_uri).rsplit(".", 1)[0] or "remediation"
    key = f"{PLAN_PREFIX}{base}.json"
    body = plan if isinstance(plan, str) else json.dumps(plan, indent=2, default=str)
    S3.put_object(
        Bucket=REPORT_BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    return {"ok": True, "planUri": f"s3://{REPORT_BUCKET}/{key}"}


_TOOLS = {
    "read_dq_insights": tool_read_dq_insights,
    "read_dq_results": tool_read_dq_results,
    "read_design": tool_read_design,
    "query_dq_history": tool_query_dq_history,
    "peek_failing_rows": tool_peek_failing_rows,
    "write_remediation_plan": tool_write_remediation_plan,
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
