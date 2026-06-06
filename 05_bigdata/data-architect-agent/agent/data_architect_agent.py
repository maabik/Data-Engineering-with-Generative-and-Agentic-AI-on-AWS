"""data-architect-agent — Strands agent on AgentCore Runtime that consumes
the data-profiling-agent's insights JSON, designs the schema for the new
dataset, registers raw + curated tables in the Glue Catalog, and runs the
first ETL job to materialise curated Parquet.

Tools live in a Lambda exposed via AgentCore Gateway over MCP-streamable-HTTP
with Cognito JWT auth. The agent calls (in order):
    - read_insights(insights_uri)
    - create_glue_database(database)
    - create_glue_table(database, table='<base>_raw',     ..., format='csv')
    - create_glue_table(database, table='<base>_curated', ..., format='parquet',
                        partitions=[...])
    - run_glue_job(job_name, role_arn, script_uri, args)
The agent then emits a single design JSON describing the result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
DATA_BUCKET = os.environ["DATA_BUCKET"]
RAW_PREFIX = os.environ.get("RAW_PREFIX", "landing/").rstrip("/") + "/"
CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "curated/").rstrip("/") + "/"
DESIGNS_PREFIX = os.environ.get("DESIGNS_PREFIX", "designs/").rstrip("/") + "/"
SCRIPT_URI = os.environ["GLUE_SCRIPT_URI"]
GLUE_ROLE_ARN = os.environ["GLUE_ROLE_ARN"]
DATABASE = os.environ.get("GLUE_DATABASE", "data_architect_agent_curated")


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
def read_insights(insights_uri: str) -> str:
    """Fetch the insights JSON written by the data-profiling-agent."""
    out = _sync(_gateway_call(_ns("read_insights"), {"insights_uri": insights_uri}))
    return json.dumps(out, indent=2)


@tool
def create_glue_database(database: str) -> str:
    """Idempotently create a Glue Catalog database."""
    out = _sync(_gateway_call(_ns("create_glue_database"), {"database": database}))
    return json.dumps(out, indent=2)


@tool
def create_glue_table(
    database: str,
    table: str,
    columns: list,
    location: str,
    partitions: list | None = None,
    format: str = "parquet",
) -> str:
    """Create or replace a Glue Catalog table.

    columns: list of {"name": ..., "type": ...}
    partitions: list of {"name": ..., "type": ...}
    format: 'csv' or 'parquet'
    """
    payload = {
        "database": database,
        "table": table,
        "columns": columns,
        "location": location,
        "partitions": partitions or [],
        "format": format,
    }
    out = _sync(_gateway_call(_ns("create_glue_table"), payload))
    return json.dumps(out, indent=2)


@tool
def run_glue_job(
    job_name: str,
    source_uri: str,
    target_uri: str,
    database: str,
    table: str,
    partition_cols: list | None = None,
    source_format: str = "csv",
) -> str:
    """Create or update the persistent Glue ETL job and run it once.

    Wires script + role from runtime env and passes the supplied
    SOURCE_URI / TARGET_URI / DATABASE / TABLE / PARTITION_COLS / SOURCE_FORMAT
    args to the curate_job.py script.
    """
    job_args = {
        "--SOURCE_URI": source_uri,
        "--TARGET_URI": target_uri,
        "--DATABASE": database,
        "--TABLE": table,
        "--PARTITION_COLS": ",".join(partition_cols or []),
        "--SOURCE_FORMAT": source_format,
    }
    payload = {
        "job_name": job_name,
        "role_arn": GLUE_ROLE_ARN,
        "script_uri": SCRIPT_URI,
        "args": job_args,
        "timeout_minutes": 30,
    }
    out = _sync(_gateway_call(_ns("run_glue_job"), payload))
    return json.dumps(out, indent=2)


SYSTEM_PROMPT = f"""You are the Data Architect Agent. You sit downstream of
the Data Profiling Agent. When invoked with the S3 URI of an insights JSON
file, your job is to turn those insights into a real catalog + curated
Parquet dataset.

Workflow (do these steps in order, calling the tools):
  1. read_insights(insights_uri). Pulls back the JSON the profiling agent
     produced. From it you have: tableName, sourceUri, schema (list of
     {{column, dataType}}), primaryKeyCandidates, partitionCandidates,
     sensitivity, dataDomain, summary.

  2. Decide the curated table's design:
       a. Pick a Glue-safe table base (snake_case, alnum + underscore).
          Use insights.tableName as the source.
       b. Map each insights schema column to a Glue/Hive type. Allowed
          types: string, int, bigint, double, boolean, date, timestamp,
          decimal(p,s). Use 'string' if unsure.
       c. Choose at most 2 partition columns. Prefer DATE-typed columns,
          then low-cardinality enums (DISPOSITION-style). Skip if none fit.
       d. Decide the curated S3 location:
          s3://{DATA_BUCKET}/{CURATED_PREFIX}<table_base>/

  3. create_glue_database('{DATABASE}').

  4. create_glue_table for the RAW table:
       database='{DATABASE}'
       table='<table_base>_raw'
       columns=<all schema columns as strings>
       location=s3 directory of the source file (the directory, not the
                file itself — strip the filename)
       partitions=[]   (raw is unpartitioned)
       format='csv'

  5. create_glue_table for the CURATED table:
       database='{DATABASE}'
       table='<table_base>_curated'
       columns=<schema columns minus partition columns, with the typed
                Glue types you chose>
       location=s3://{DATA_BUCKET}/{CURATED_PREFIX}<table_base>/
       partitions=<your typed partition columns>
       format='parquet'

  6. run_glue_job to materialise the curated dataset:
       job_name='{os.environ.get("PREFIX", "data-architect-agent")}-curate-<table_base>'
       source_uri=insights.sourceUri
       target_uri=s3://{DATA_BUCKET}/{CURATED_PREFIX}<table_base>/
       database='{DATABASE}'
       table='<table_base>_curated'
       partition_cols=<list of partition column names you chose>
       source_format='csv' (or json/parquet — infer from source URI suffix)

  7. Return ONE JSON object (and nothing else outside it) with these keys:
       database, rawTable, curatedTable, location, partitionKeys (array of
       {{name, type}}), columns (array of typed {{name, type}}),
       glueJob (the run_glue_job result), insightsUri, sourceUri, summary
       (one paragraph: what design choices you made and why).

Rules:
  * Never invent values not in the insights.
  * If any tool returns ok=false, include the error in your final JSON
    under "error" and stop.
  * Your FINAL message must START with `{{` and END with `}}`. No preamble,
    no markdown fences, no commentary outside the JSON object.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[read_insights, create_glue_database, create_glue_table, run_glue_job],
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


def _maybe_persist_design(insights_uri: str, text: str) -> str | None:
    payload = _extract_json_object(text)
    if payload is None:
        logger.warning("agent output not JSON; skipping design persistence")
        return None
    base = os.path.basename(insights_uri).rsplit(".", 1)[0] or "design"
    key = f"{DESIGNS_PREFIX}{base}.json"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=DATA_BUCKET,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{DATA_BUCKET}/{key}"


@app.entrypoint
def invoke(payload):
    insights_uri = payload.get("insights_uri") or payload.get("prompt", "")
    if not insights_uri:
        return json.dumps({"error": "missing insights_uri in payload"})
    user_prompt = (
        f"A new insights file has landed at {insights_uri}. Design the schema, "
        f"register the raw and curated tables in Glue, and run the curate job. "
        f"Then emit the design JSON per your instructions."
    )
    agent = _make_agent()
    response = agent(user_prompt)
    text = response.message["content"][0]["text"]
    persisted = _maybe_persist_design(insights_uri, text)
    if persisted:
        logger.info("design persisted to %s", persisted)
        obj = _extract_json_object(text)
        if obj is not None:
            obj["designUri"] = persisted
            return json.dumps(obj, indent=2)
    return text


if __name__ == "__main__":
    app.run()
