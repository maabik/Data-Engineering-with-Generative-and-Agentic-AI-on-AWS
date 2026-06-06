"""data-quality-deequ-agent — Strands agent on AgentCore Runtime that
runs Deequ-based data quality checks against a curated table the
data-architect-agent registered, then derives an insights report.

Workflow:
    1. read_design(design_uri)              -> design JSON from data-architect-agent
    2. run_deequ_job(database, table, …)    -> Glue job with Deequ
    3. register_report_table(...)           -> Athena-queryable table
    4. query_report(...)                    -> per-check pass/fail rows
    5. derive_quality_insights(run_id, …)   -> slim summary
    6. Emit ONE JSON object with the quality verdict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import timedelta
from typing import Any

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_CREDS_SECRET = os.environ["GATEWAY_CREDS_SECRET"]
GATEWAY_TARGET_NAME = os.environ.get("GATEWAY_TARGET_NAME", "tools")
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
REPORT_PREFIX = os.environ.get("REPORT_PREFIX", "dq-reports/").rstrip("/") + "/"
INSIGHTS_PREFIX = os.environ.get("DQ_INSIGHTS_PREFIX", "dq-insights/").rstrip("/") + "/"
DQ_DATABASE = os.environ.get("DQ_DATABASE", "data_quality_deequ_agent_dq")
DQ_TABLE = os.environ.get("DQ_TABLE", "dq_results")


def _get_bearer_token() -> str:
    secret = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
        SecretId=GATEWAY_CREDS_SECRET
    )
    creds = json.loads(secret["SecretString"])
    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


async def _gateway_call(tool_name: str, args: dict) -> Any:
    token = _get_bearer_token()
    headers = {
        "authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with streamablehttp_client(
        GATEWAY_URL, headers, timeout=timedelta(seconds=1800)
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name=tool_name, arguments=args)
            if result.isError:
                return {"error": result.content[0].text}
            content = result.content[0].text
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {"raw": content}


def _sync(coro):
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ns(name: str) -> str:
    return f"{GATEWAY_TARGET_NAME}___{name}"


@tool
def read_design(design_uri: str) -> str:
    """Fetch the data-architect-agent's design JSON for the curated table."""
    out = _sync(_gateway_call(_ns("read_design"), {"design_uri": design_uri}))
    return json.dumps(out, indent=2)


@tool
def run_deequ_job(database: str, table: str, run_id: str) -> str:
    """Start the persistent Deequ Glue job against the given curated table.
    Polls until terminal. Returns suggestionsUri / resultsUri / parquetPrefix."""
    out = _sync(
        _gateway_call(
            _ns("run_deequ_job"),
            {"database": database, "table": table, "run_id": run_id},
        )
    )
    return json.dumps(out, indent=2)


@tool
def register_report_table(source_table: str) -> str:
    """Register / refresh the Athena-queryable Glue table over the
    Parquet output for `source_table`'s DQ runs."""
    out = _sync(
        _gateway_call(
            _ns("register_report_table"),
            {"source_table": source_table, "database": DQ_DATABASE, "table": DQ_TABLE},
        )
    )
    return json.dumps(out, indent=2)


@tool
def query_report(query: str) -> str:
    """Run an Athena query against the data-quality-deequ-agent workgroup. Use SELECTs on
    {DQ_DATABASE}.{DQ_TABLE} to inspect per-constraint pass/fail."""
    out = _sync(_gateway_call(_ns("query_report"), {"query": query}))
    return json.dumps(out, indent=2)


@tool
def derive_quality_insights(run_id: str, database: str, table: str) -> str:
    """Slim summary of a Deequ run: passed/failed counts, top failing checks."""
    out = _sync(
        _gateway_call(
            _ns("derive_quality_insights"),
            {"run_id": run_id, "database": database, "table": table},
        )
    )
    return json.dumps(out, indent=2)


SYSTEM_PROMPT = f"""You are the Data Quality Agent, powered by Deequ. You
sit downstream of the Data Architect Agent. When invoked with the S3 URI
of a design JSON, your job is to assess the curated table's quality and
emit an actionable report.

Workflow (do these steps in order, calling the tools):
  1. read_design(design_uri). Extract `database` and `curatedTable`.

  2. Pick a unique `run_id` (use the design's basename + a timestamp if
     useful). Keep it lowercase alnum + underscore.

  3. run_deequ_job(database=<db>, table=<curatedTable>, run_id=<run_id>).
     The Glue job runs Deequ ConstraintSuggestionRunner, filters out
     overly aggressive completeness checks, applies the safe constraints
     via VerificationSuite, and writes JSON + Parquet reports.
     If ok=false, include the error and stop.

  4. register_report_table(source_table=<curatedTable>). This makes the
     parquet results queryable from Athena at {DQ_DATABASE}.{DQ_TABLE},
     partitioned by run_id (Athena partition projection — no MSCK needed).

  5. query_report(...). Run an Athena query like:
       SELECT constraint, constraint_status, constraint_message
       FROM "{DQ_DATABASE}"."{DQ_TABLE}"
       WHERE run_id = '<run_id>'
       ORDER BY constraint_status;
     This validates the round-trip from S3 -> Glue -> Athena.

  6. derive_quality_insights(run_id, database, table). Confirms the
     overall pass/fail counts and the top failing checks.

  7. Return ONE JSON object (and nothing else outside it) with:
       database, table, runId, suggestionsUri, resultsUri, parquetPrefix,
       overallStatus, totalChecks, passedCount, failedCount,
       topFailing (array of {{constraint, constraint_status, constraint_message}}),
       athena: {{database, table, sampleQuery, sampleRows}},
       summary (one paragraph: which constraints passed, which failed,
       what that means for downstream consumers, and whether this dataset
       is fit-for-use as-is).

Rules:
  * Never invent values not in the tool outputs.
  * If any tool returns ok=false, include the error in your final JSON
    under "error" and stop.
  * Your FINAL message must START with `{{` and END with `}}`. No preamble,
    no markdown fences, no commentary outside the JSON object.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            read_design,
            run_deequ_job,
            register_report_table,
            query_report,
            derive_quality_insights,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


app = BedrockAgentCoreApp()


def _extract_json_object(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _maybe_persist_dq_insights(design_uri: str, text: str) -> str | None:
    payload = _extract_json_object(text)
    if payload is None:
        logger.warning("agent output not JSON; skipping insights persistence")
        return None
    base = os.path.basename(design_uri).rsplit(".", 1)[0] or "dq"
    key = f"{INSIGHTS_PREFIX}{base}.json"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=REPORT_BUCKET,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{REPORT_BUCKET}/{key}"


@app.entrypoint
def invoke(payload):
    design_uri = payload.get("design_uri") or payload.get("prompt", "")
    if not design_uri:
        return json.dumps({"error": "missing design_uri in payload"})
    user_prompt = (
        f"A new design JSON has landed at {design_uri}. Run Deequ data "
        f"quality checks against the curated table it describes, register "
        f"the results in Athena, and emit the quality insights JSON per "
        f"your instructions."
    )
    agent = _make_agent()
    response = agent(user_prompt)
    text = response.message["content"][0]["text"]
    persisted = _maybe_persist_dq_insights(design_uri, text)
    if persisted:
        logger.info("dq insights persisted to %s", persisted)
        obj = _extract_json_object(text)
        if obj is not None:
            obj["dqInsightsUri"] = persisted
            return json.dumps(obj, indent=2)
    return text


if __name__ == "__main__":
    app.run()
