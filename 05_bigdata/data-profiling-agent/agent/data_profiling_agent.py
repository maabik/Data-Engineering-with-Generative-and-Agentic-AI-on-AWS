"""data-profiling-agent — Strands agent on AgentCore Runtime that profiles a
new dataset landing in the data-lake landing zone and emits a structured
insights report.

Tools live in a Lambda exposed via AgentCore Gateway over MCP-streamable-HTTP
with Cognito JWT auth. The agent calls:
    - profile_dataset(s3_uri)            -> kicks DataBrew profile job, returns reportUri
    - analyze_profiling_report(report_uri) -> slim per-column stats

The agent then synthesizes an insights JSON: schema, primary key candidates,
partition candidates, indexing recommendations, and PII / sensitivity flags.
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
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_CREDS_SECRET = os.environ["GATEWAY_CREDS_SECRET"]
GATEWAY_TARGET_NAME = os.environ.get("GATEWAY_TARGET_NAME", "tools")
INSIGHTS_BUCKET = os.environ.get("INSIGHTS_BUCKET")
INSIGHTS_PREFIX = os.environ.get("INSIGHTS_PREFIX", "insights/").rstrip("/") + "/"


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
        GATEWAY_URL, headers, timeout=timedelta(seconds=900)
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
def profile_dataset(s3_uri: str) -> str:
    """Create + run an AWS Glue DataBrew FULL_DATASET profile job for the
    given S3 object and return the report's S3 URI. Blocks until the job
    finishes (typically 1-3 minutes for small CSV files)."""
    out = _sync(_gateway_call(_ns("profile_dataset"), {"s3_uri": s3_uri}))
    return json.dumps(out, indent=2)


@tool
def analyze_profiling_report(report_uri: str) -> str:
    """Fetch the DataBrew profile JSON at `report_uri` (S3) and return a
    slim, model-friendly view: dataset-level stats + per-column statistics
    with bulky histograms removed."""
    out = _sync(_gateway_call(_ns("analyze_profiling_report"), {"report_uri": report_uri}))
    return json.dumps(out, indent=2)


SYSTEM_PROMPT = """You are the Data Profiling Agent. You sit at the Landing
Zone of a data lake. When invoked with an S3 URI of a newly arrived dataset,
your job is to drive a profiling pipeline and emit structured insights for
the next agent (the Data Architect Agent) to consume.

Workflow (do these steps in order, calling the tools):
  1. Call profile_dataset(s3_uri). It runs an AWS Glue DataBrew FULL_DATASET
     profile job and returns a reportUri (S3) when done.
  2. Call analyze_profiling_report(report_uri) to fetch the slim per-column
     statistics.
  3. Synthesize insights from the slim report. For each column, derive:
       - dataType (best-guess SQL type)
       - nullPct, distinctCount, uniqueValuesCount
       - cardinality (low/medium/high) based on distinctCount/sampleSize
       - statistical anomalies (skewness, outlier ratio, missing %)
  4. Recommend:
       - primaryKeyCandidates: unique + non-null columns
       - partitionCandidates: DATE-typed and low-cardinality columns
       - indexCandidates: high-cardinality string columns frequently used
         as identifiers (DOCKET, ID, NUMBER patterns)
       - sensitivity: flag columns with names matching PII patterns
         (NAME, EMAIL, PHONE, SSN, ADDRESS, DOB, RESPONDENTS, etc.)
       - dataDomain: a short domain label inferred from column names
  5. Return ONE JSON object (and nothing else outside it) with these keys:
       tableName, sourceUri, reportUri, sampleSize, schema (array of column
       objects), primaryKeyCandidates, partitionCandidates, indexCandidates,
       sensitivity (array of {column, type, reason}), dataDomain, summary
       (one-paragraph human-readable executive summary).

Rules:
  * Never invent values not in the report.
  * If a tool returns ok=false, include the error in your final JSON under
    "error" and stop.
  * Your FINAL message must START with `{` and END with `}`. No preamble
    ("Let me compile..."), no postscript, no markdown fences, no commentary
    of any kind. The very first character is `{`.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[profile_dataset, analyze_profiling_report],
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


def _maybe_persist_insights(s3_uri: str, text: str) -> str | None:
    if not INSIGHTS_BUCKET:
        return None
    payload = _extract_json_object(text)
    if payload is None:
        logger.warning("agent output not JSON; skipping insights persistence")
        return None
    base = os.path.basename(s3_uri).rsplit(".", 1)[0] or "dataset"
    key = f"{INSIGHTS_PREFIX}{base}.json"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=INSIGHTS_BUCKET,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{INSIGHTS_BUCKET}/{key}"


@app.entrypoint
def invoke(payload):
    s3_uri = payload.get("s3_uri") or payload.get("prompt", "")
    if not s3_uri:
        return json.dumps({"error": "missing s3_uri in payload"})
    user_prompt = (
        f"A new dataset has landed at {s3_uri}. Profile it, analyze the "
        f"report, and emit the insights JSON per your instructions."
    )
    agent = _make_agent()
    response = agent(user_prompt)
    text = response.message["content"][0]["text"]
    persisted = _maybe_persist_insights(s3_uri, text)
    if persisted:
        logger.info("insights persisted to %s", persisted)
        obj = _extract_json_object(text)
        if obj is not None:
            obj["insightsUri"] = persisted
            return json.dumps(obj, indent=2)
    return text


if __name__ == "__main__":
    app.run()
