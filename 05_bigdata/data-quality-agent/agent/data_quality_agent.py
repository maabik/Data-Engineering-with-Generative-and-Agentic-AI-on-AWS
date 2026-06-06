"""data-quality-agent — Strands agent on AgentCore Runtime that turns
a Deequ insights JSON into an actionable remediation plan.

Workflow:
    1. read_dq_insights(insights_uri)  -> agent's slim summary
    2. read_dq_results(results_uri)    -> full per-constraint rows
    3. read_design(design_uri)         -> column types, partitions, location
    4. query_dq_history(table)         -> trend / drift signal
    5. peek_failing_rows(...)          -> only when a constraint failed
    6. write_remediation_plan(...)
    7. Emit ONE JSON object: the plan.
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
REPORT_BUCKET = os.environ["REPORT_BUCKET"]
PLAN_PREFIX = os.environ.get("PLAN_PREFIX", "dq-remediation/").rstrip("/") + "/"


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
        GATEWAY_URL, headers, timeout=timedelta(seconds=600)
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
def read_dq_insights(insights_uri: str) -> str:
    """Fetch the slim insights JSON written by data-quality-deequ-agent."""
    return json.dumps(_sync(_gateway_call(_ns("read_dq_insights"), {"insights_uri": insights_uri})), indent=2)


@tool
def read_dq_results(results_uri: str) -> str:
    """Fetch the full Deequ results JSON for a single run (per-constraint rows + completeness)."""
    return json.dumps(_sync(_gateway_call(_ns("read_dq_results"), {"results_uri": results_uri})), indent=2)


@tool
def read_design(design_uri: str) -> str:
    """Fetch the data-architect-agent design JSON for the curated table."""
    return json.dumps(_sync(_gateway_call(_ns("read_design"), {"design_uri": design_uri})), indent=2)


@tool
def query_dq_history(table: str, limit: int = 20) -> str:
    """Last N runs for `table` from the dq_results Athena table:
    run_id, total, passed, failed, last_ts. Use to check trend / drift."""
    return json.dumps(
        _sync(_gateway_call(_ns("query_dq_history"), {"table": table, "limit": limit})),
        indent=2,
    )


@tool
def peek_failing_rows(database: str, table: str, predicate: str, limit: int = 10) -> str:
    """SELECT * FROM database.table WHERE <predicate> LIMIT <=50.
    Use only after reading the design — confirms which actual rows triggered
    a failed constraint. Predicate must NOT contain ';', '--', or '/*'."""
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("peek_failing_rows"),
                {"database": database, "table": table, "predicate": predicate, "limit": limit},
            )
        ),
        indent=2,
    )


@tool
def write_remediation_plan(insights_uri: str, plan: str) -> str:
    """Persist the final remediation plan JSON to s3://${BUCKET}/dq-remediation/."""
    parsed: Any = plan
    if isinstance(plan, str):
        try:
            parsed = json.loads(plan)
        except json.JSONDecodeError:
            parsed = plan
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("write_remediation_plan"),
                {"insights_uri": insights_uri, "plan": parsed},
            )
        ),
        indent=2,
    )


SYSTEM_PROMPT = """You are the Data Quality Remediation Agent. You sit
downstream of the Data Quality Deequ Agent. Given the S3 URI of a
dq-insights JSON, your job is to produce an *actionable* remediation
plan a data engineer can pick up and execute.

Workflow (follow in order):

  1. read_dq_insights(insights_uri). Pull out: database, table, runId,
     overallStatus, totalChecks, passedCount, failedCount, topFailing,
     resultsUri, suggestionsUri. If the agent already failed upstream
     (insights.error present), skip directly to step 7 and emit a plan
     focused on the upstream root cause.

  2. read_dq_results(insights.resultsUri). Look at every failed row in
     `results.checks` to confirm which constraints actually failed and
     read their `constraint_message`. Also note `completeness` ratios
     (in the results JSON) for any column whose Deequ check failed.

  3. If the insights JSON references a design (e.g. via the upstream
     architect agent), read_design(design_uri). Use the design columns
     and partition keys to translate Deequ check names like
     `CompletenessConstraint(Completeness(fine,...))` back into the
     business-meaning column.

  4. query_dq_history(table=<curatedTable>, limit=10). Look at trend:
       - first-time failure (regression) vs recurring (chronic)
       - pass rate trajectory across recent runs

  5. For each failed constraint where reading the actual data would
     materially help (NULLness, range violations, uniqueness), call
     peek_failing_rows(database=<db>, table=<curatedTable>,
     predicate="<col> IS NULL" or "<col> < 0" etc., limit=10). Skip
     this if no constraints failed (overallStatus='Success'); a clean
     run gets a brief plan that says so.

  6. write_remediation_plan(insights_uri, <plan_json>). The plan_json
     MUST have these top-level keys:
       - database, table, runId
       - verdict: one of "ready", "remediable", "blocked"
       - severity: "info" | "low" | "medium" | "high" | "critical"
       - rootCauses: array of {{ id, constraint, column?, evidence,
           hypothesis, sample (array of objects, may be empty),
           confidence: "low|medium|high" }}
       - remediationSteps: array of {{ priority, owner, action,
           detail, estimatedEffort }} ordered by priority (1 = first)
       - reRunGuidance: short string saying when to re-run data-quality-deequ-agent
       - history: {{ recentRuns, trend: "improving|stable|degrading" }}
       - summary: one-paragraph executive summary

  7. Return ONE JSON object (and nothing else outside it) — the same
     plan you wrote, with an extra `planUri` field set to the URI
     write_remediation_plan returned.

Rules:
  * Never invent constraint names, column values, or row counts not in
    the tool outputs.
  * If a tool returns ok=false, include the error in your final JSON
    under "error" — do not retry blindly more than once.
  * If overallStatus is 'Success' and failedCount == 0, set verdict =
    "ready", severity = "info", rootCauses = [], remediationSteps = []
    (or a single low-priority "monitor" step), and write a clear
    summary saying the table is fit-for-use.
  * Your FINAL message must START with `{` and END with `}`. No
    preamble, no markdown fences, no commentary outside the JSON
    object.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            read_dq_insights,
            read_dq_results,
            read_design,
            query_dq_history,
            peek_failing_rows,
            write_remediation_plan,
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


@app.entrypoint
def invoke(payload):
    insights_uri = payload.get("insights_uri") or payload.get("prompt", "")
    if not insights_uri:
        return json.dumps({"error": "missing insights_uri in payload"})
    user_prompt = (
        f"A new dq-insights JSON has landed at {insights_uri}. Produce a "
        f"remediation plan per your instructions."
    )
    agent = _make_agent()
    response = agent(user_prompt)
    text = response.message["content"][0]["text"]
    obj = _extract_json_object(text)
    if obj is not None:
        return json.dumps(obj, indent=2)
    return text


if __name__ == "__main__":
    app.run()
