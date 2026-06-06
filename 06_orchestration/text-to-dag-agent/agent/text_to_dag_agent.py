"""text-to-dag-agent — Strands agent on AgentCore Runtime that turns a natural-language
pipeline description into either a Step Functions ASL definition (and deploys
it) or an Airflow DAG, plus an IAM policy.

The agent does not hold the tool implementations itself; AgentCore Gateway
exposes the four Lambda-backed MCP tools and the agent connects via MCP-over-HTTP
using a Cognito client-credentials bearer token.
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
        GATEWAY_URL, headers, timeout=timedelta(seconds=120)
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
    """Gateway prefixes tool names with the target name + '___'."""
    target = os.environ.get("GATEWAY_TARGET_NAME", "tools")
    return f"{target}___{name}"


@tool
def generate_asl(description: str) -> str:
    """Generate an AWS Step Functions ASL JSON definition for the pipeline."""
    out = _sync(_gateway_call(_ns("generate_asl"), {"description": description}))
    return json.dumps(out, indent=2)


@tool
def generate_airflow_dag(description: str) -> str:
    """Generate an Apache Airflow 2.x DAG (Python source)."""
    out = _sync(_gateway_call(_ns("generate_airflow_dag"), {"description": description}))
    return out.get("dag_python", json.dumps(out))


@tool
def generate_iam_policy(definition_json: str) -> str:
    """Generate a least-priv IAM policy for the given ASL definition (JSON string)."""
    out = _sync(
        _gateway_call(_ns("generate_iam_policy"), {"definition": definition_json})
    )
    return json.dumps(out, indent=2)


@tool
def validate_asl(definition_json: str) -> str:
    """Validate a Step Functions ASL definition: structural rules + Resource allowlist + AWS lint diagnostics."""
    out = _sync(_gateway_call(_ns("validate_asl"), {"definition": definition_json}))
    return json.dumps(out, indent=2)


@tool
def validate_airflow_dag(dag_python: str) -> str:
    """AST-validate an Airflow DAG (Python source). Returns syntax/structural errors + extracted dag_id, schedule, task_ids."""
    out = _sync(_gateway_call(_ns("validate_airflow_dag"), {"dag_python": dag_python}))
    return json.dumps(out, indent=2)


@tool
def deploy_sfn(name: str, definition_json: str) -> str:
    """Deploy (create or update) a Step Functions state machine. Returns the ARN."""
    out = _sync(
        _gateway_call(
            _ns("deploy_sfn"),
            {"name": name, "definition": definition_json},
        )
    )
    return json.dumps(out, indent=2)


SYSTEM_PROMPT = """You are text-to-dag-agent, an orchestration-engineering agent. Your
job is to turn a natural-language pipeline description from the user into a
deployable workflow.

Decide which target the user wants:
  * Step Functions (default for AWS-native flows) - call generate_asl, then
    validate_asl. If validation passes and the user asked you to deploy, call
    deploy_sfn with a sensible name derived from the description. Always also
    call generate_iam_policy with the validated ASL so the user has the role
    policy.
  * Airflow - call generate_airflow_dag, then validate_airflow_dag. If the
    validator reports errors, regenerate once. Also call generate_iam_policy
    with the closest equivalent ASL if the user wants AWS perms.

Return the artifacts inline in your final reply, separated by clear ASCII
section headers. Do NOT invent ARNs - the tools handle deployment. If
validate_asl reports errors, regenerate once and try again before giving up."""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            generate_asl,
            generate_airflow_dag,
            generate_iam_policy,
            validate_asl,
            validate_airflow_dag,
            deploy_sfn,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    user_input = payload.get("prompt", "")
    agent = _make_agent()
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
