"""data-contract-agent — Strands agent on AgentCore Runtime that consumes
the data-profiling-agent's insights JSON and either AUTHORS the first
data contract for a dataset or ENFORCES an existing one.

Two modes, auto-detected from whether a contract already exists:

  AUTHOR  (no s3://.../contracts/<table>.yaml yet)
    1. read_insights(insights_uri)
    2. read_contract(table)              -> exists=false
    3. Draft a dbt-flavoured contract from the insights (columns, types,
       required/unique/pattern/min/max where the profile supports it).
    4. write_contract(table, contract)
    5. compile_contract_to_deequ_checks(table, contract)
    6. write_verdict(insights_uri, {status:"ok", mode:"author", ...})

  ENFORCE (contract exists)
    1. read_insights(insights_uri)
    2. read_contract(table)              -> exists=true, contract=...
    3. describe_glue_table(database, table)
    4. diff_contract_vs_table(contract, glue_table)
    5. compile_contract_to_deequ_checks(table)        # refresh on every run
    6. write_verdict(insights_uri, {status, mode:"enforce", findings, ...})

The agent does NOT block downstream work. The verdict is advisory; the
chapter-12 orchestrator (or a human) decides whether to act on a
breaking finding.
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
CONTRACTS_PREFIX = os.environ.get("CONTRACTS_PREFIX", "contracts/").rstrip("/") + "/"
VERDICTS_PREFIX = os.environ.get("VERDICTS_PREFIX", "contract-verdicts/").rstrip("/") + "/"
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
def read_contract(table: str) -> str:
    """Fetch the YAML contract for a table from s3://.../contracts/<table>.yaml.
    Returns exists=false (not an error) when no contract has been authored yet.
    """
    out = _sync(_gateway_call(_ns("read_contract"), {"table": table}))
    return json.dumps(out, indent=2)


@tool
def write_contract(table: str, contract: dict) -> str:
    """Persist a contract dict as YAML at s3://.../contracts/<table>.yaml.

    Expected contract shape:
      table: <name>
      description: <short prose>
      columns:
        - name: id
          type: bigint
          required: true
          unique: true
        - name: status
          type: string
          allowed_values: [open, closed]
      deprecated_columns: []
    """
    out = _sync(_gateway_call(_ns("write_contract"), {"table": table, "contract": contract}))
    return json.dumps(out, indent=2)


@tool
def describe_glue_table(database: str, table: str) -> str:
    """Return the live Glue table's columns, partition keys, description,
    location, and parameters. exists=false if the table doesn't exist yet.
    """
    out = _sync(_gateway_call(_ns("describe_glue_table"), {"database": database, "table": table}))
    return json.dumps(out, indent=2)


@tool
def diff_contract_vs_table(contract: dict, glue_table: dict) -> str:
    """Compare a contract dict against a describe_glue_table result. Returns
    {status, totalFindings, breaking, warning, info, findings[]}, where each
    finding has severity in {breaking, warning, info}. Pure function — no S3."""
    out = _sync(_gateway_call(_ns("diff_contract_vs_table"), {"contract": contract, "glue_table": glue_table}))
    return json.dumps(out, indent=2)


@tool
def compile_contract_to_deequ_checks(table: str, contract: dict | None = None) -> str:
    """Write a Deequ-flavoured checks JSON for a contract to
    s3://.../contract-deequ-checks/<table>.json. The data-quality-deequ-agent
    can pick this up to drive richer constraint verification than its own
    ConstraintSuggestionRunner defaults."""
    payload: dict = {"table": table}
    if contract is not None:
        payload["contract"] = contract
    out = _sync(_gateway_call(_ns("compile_contract_to_deequ_checks"), payload))
    return json.dumps(out, indent=2)


@tool
def write_verdict(insights_uri: str, verdict: dict) -> str:
    """Persist the agent's final verdict object at
    s3://.../contract-verdicts/<file>.json. verdict must include 'status'
    in {ok, warning, breaking} and 'mode' in {author, enforce}."""
    out = _sync(_gateway_call(_ns("write_verdict"), {"insights_uri": insights_uri, "verdict": verdict}))
    return json.dumps(out, indent=2)


SYSTEM_PROMPT = f"""You are the Data Contract Agent. You sit BEFORE the
Data Architect Agent in the medallion chain — the architect agent will
soon take this same insights file and lock a Glue schema in. Your job is
to give the producer a versioned, machine-readable spec for what this
dataset should look like, and (on subsequent runs) to flag breaking
changes before they propagate.

Two modes — auto-detect:

  AUTHOR  (read_contract returns exists=false)
    a. Draft a dbt-flavoured YAML contract from the insights JSON.
       For every column in insights.schema, emit:
         - name: <column> (snake_case, alnum + underscore)
           type: <one of: string, int, bigint, double, boolean, date,
                  timestamp, decimal(p,s)>
           required: <true if profile shows <1% null, else false>
       Also fill in:
         description: <one-line dataset description from insights.summary>
         table:       <insights.tableName, snake_cased>
         deprecated_columns: []
       Optionally add unique/allowed_values/pattern when the profile
       clearly supports it (e.g., a column with 100% distinct + few rows
       isn't necessarily unique — be conservative; don't invent).
    b. Call write_contract(table, contract).
    c. Call compile_contract_to_deequ_checks(table, contract).
    d. Call write_verdict with:
         {{"status":"ok", "mode":"author", "table":..., "contractUri":...,
           "checksUri":..., "summary":"first contract authored from profile"}}

  ENFORCE  (read_contract returns exists=true)
    a. Call describe_glue_table('{DATABASE}', table). If the table doesn't
       exist yet (the architect hasn't run), set live={{exists:false}} so the
       diff still runs and emits a 'table_missing' finding (which is
       expected on the first profile of a brand-new dataset).
    b. Call diff_contract_vs_table(contract, live).
    c. Call compile_contract_to_deequ_checks(table) to refresh checks.
    d. Call write_verdict with:
         {{"status":<diff.status>, "mode":"enforce", "table":...,
           "contractUri":..., "checksUri":..., "totalFindings":...,
           "breaking":..., "warning":..., "info":..., "findings":[...],
           "summary":"<one paragraph: what changed and what to do about it>"}}

Rules:
  * Never fabricate columns or types not in the insights.
  * 'breaking' findings (column_missing on a required column, type_changed,
    table_missing) are advisory — you do NOT halt the pipeline. The
    orchestrator decides what to do.
  * Always call write_verdict last, regardless of mode.
  * Your FINAL message must START with `{{` and END with `}}` and be a
    single JSON object summarising what you did. No preamble, no markdown.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            read_insights,
            read_contract,
            write_contract,
            describe_glue_table,
            diff_contract_vs_table,
            compile_contract_to_deequ_checks,
            write_verdict,
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
        f"A new insights file has landed at {insights_uri}. Author or enforce "
        f"its data contract per your instructions, then emit the verdict JSON."
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
