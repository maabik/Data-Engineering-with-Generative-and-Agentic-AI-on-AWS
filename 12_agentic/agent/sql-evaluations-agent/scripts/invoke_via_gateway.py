#!/usr/bin/env python3
"""Invoke the SQL Evaluations Agent through AgentCore Gateway as an MCP tool."""

import asyncio
import json
import os
import sys

import boto3
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


REGION = os.environ.get("AWS_REGION", "us-east-1")
SECRET_NAME = os.environ.get("SECRET_NAME", "sql-evaluations-agent/gateway-credentials")
GATEWAY_URL = os.environ["GATEWAY_URL"]


def _bearer_token() -> str:
    secrets = boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=SECRET_NAME)
    creds = json.loads(secrets["SecretString"])
    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]


async def evaluate(sql: str, context: str = "", dialect: str = "ansi"):
    token = _bearer_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(GATEWAY_URL, headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools advertised by gateway:")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")
            # Gateway namespaces tool names with the target name. Discover the
            # qualified name dynamically rather than hard-coding "evaluations___".
            tool_name = next(
                (t.name for t in tools.tools if t.name.endswith("evaluate_sql")),
                "evaluate_sql",
            )
            result = await session.call_tool(tool_name, {"sql": sql, "context": context, "dialect": dialect})
            for c in result.content:
                if hasattr(c, "text"):
                    print(c.text)


if __name__ == "__main__":
    sql = sys.stdin.read() if not sys.stdin.isatty() else (sys.argv[1] if len(sys.argv) > 1 else "")
    if not sql.strip():
        print("usage: invoke_via_gateway.py '<SQL>'", file=sys.stderr)
        sys.exit(1)
    asyncio.run(evaluate(sql))
