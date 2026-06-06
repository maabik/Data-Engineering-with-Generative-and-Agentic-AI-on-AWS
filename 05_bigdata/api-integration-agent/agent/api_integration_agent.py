"""api-integration-agent — Strands agent on AgentCore Runtime that
takes a dataset description and (optionally) an API spec or
Secrets-Manager-stored API record, then produces, deploys, schedules,
and live-tests a Lambda that lands data in the data lake landing zone.

Workflow:
    1. (optional) list_api_secrets / read_api_secret — discover the
       endpoint, auth shape, and docs/schema the engineer pre-loaded.
       PUBLIC APIs (no auth) are also fully supported: just pass an
       endpoint and skip the secret.
    2. write_lambda_code — emit lambda_function.py for python3.12.
    3. deploy_lambda — create/update the Lambda + EventBridge schedule.
    4. invoke_lambda_test — fire it once, confirm an object landed.
    5. write_integration_report — persist a JSON summary.
    6. Return ONE JSON object describing the deployed integration.
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
LANDING_PREFIX = os.environ.get("LANDING_PREFIX", "landing/").rstrip("/") + "/"


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
def list_api_secrets() -> str:
    """List the Secrets Manager API records the agent can ingest from."""
    return json.dumps(_sync(_gateway_call(_ns("list_api_secrets"), {})), indent=2)


@tool
def read_api_secret(secret_name: str) -> str:
    """Read one API record from Secrets Manager. Credentials are redacted in
    the response — only their presence is reported. The deployed Lambda
    pulls the credential at runtime."""
    return json.dumps(
        _sync(_gateway_call(_ns("read_api_secret"), {"secret_name": secret_name})),
        indent=2,
    )


@tool
def write_lambda_code(dataset: str, code: str, requirements: str = "") -> str:
    """Persist the agent-authored Python source to S3. `code` MUST define
    `lambda_handler(event, context)` and target python3.12. The deployed
    Lambda has only the standard library + boto3 available unless the
    requirements helper is also written (informational only, this Lambda
    is currently zip-deployed without extra layers)."""
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("write_lambda_code"),
                {"dataset": dataset, "code": code, "requirements": requirements},
            )
        ),
        indent=2,
    )


@tool
def deploy_lambda(
    dataset: str,
    code_uri: str,
    secret_name: str = "",
    schedule: str = "rate(1 hour)",
    env: str = "{}",
) -> str:
    """Create or update the integration Lambda and its EventBridge schedule.

    `secret_name` is OPTIONAL — pass "" (empty string) for PUBLIC APIs
    that don't require auth. `env` is a JSON object string of extra env
    vars (e.g. `{"API_ENDPOINT": "https://..."}` for public APIs). The
    function is named `api-integration-agent-ing-<dataset>` and the rule `api-integration-agent-ing-<dataset>`."""
    try:
        env_obj = json.loads(env) if env else {}
    except json.JSONDecodeError:
        env_obj = {}
    args: dict[str, Any] = {
        "dataset": dataset,
        "code_uri": code_uri,
        "schedule": schedule,
        "env": env_obj,
    }
    if secret_name:
        args["secret_name"] = secret_name
    return json.dumps(_sync(_gateway_call(_ns("deploy_lambda"), args)), indent=2)


@tool
def invoke_lambda_test(dataset: str) -> str:
    """Synchronously invoke the deployed Lambda once with a manual-test
    payload, then list the most-recent objects under landing/<dataset>/."""
    return json.dumps(
        _sync(_gateway_call(_ns("invoke_lambda_test"), {"dataset": dataset})),
        indent=2,
    )


@tool
def write_integration_report(dataset: str, report: str) -> str:
    """Persist the final integration report JSON."""
    parsed: Any = report
    if isinstance(report, str):
        try:
            parsed = json.loads(report)
        except json.JSONDecodeError:
            parsed = report
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("write_integration_report"),
                {"dataset": dataset, "report": parsed},
            )
        ),
        indent=2,
    )


SYSTEM_PROMPT = """You are the API Integration Agent. Given a target
dataset name and one of:
  (a) a `secret_name` pointing to a Secrets Manager record this agent
      pre-loaded under `api-integration-agent/apis/`, OR
  (b) an inline `api_spec` describing the endpoint, auth, and the data
      the user wants (PUBLIC APIs with NO authentication are fully
      supported — just an `endpoint` and a description is enough),
you must produce a fully deployed AWS Lambda that pulls data from the
API on a schedule and lands it in the data lake's landing zone.

Workflow (strict order):

  1. Inspect inputs. If a `secret_name` is provided, call
     read_api_secret(secret_name). The credential value is redacted
     server-side; you'll see `hasCredential` and the field name (e.g.
     "api_key"). Other fields you may see: `endpoint`, `auth_type`
     (one of: "none", "bearer", "api_key_header", "api_key_query",
     "basic"), `header_name`, `query_param`, `dataset_hint`,
     `default_path`, `default_query`, `pagination`, `notes`, `schema`.
     If `secret_name` is missing, work from the inline `api_spec`.
     Public-API path: when `auth_type` is "none" OR neither a secret
     nor a credential is needed, the Lambda must NOT attempt to read
     a Secrets Manager value — it just performs the HTTP GET directly.

  2. Generate Python source for the Lambda. Required shape:

       import json, os, uuid, urllib.request, urllib.parse
       from datetime import datetime, timezone
       import boto3

       S3 = boto3.client("s3")
       SECRETS = boto3.client("secretsmanager")  # only if auth needed

       DATA_BUCKET   = os.environ["DATA_BUCKET"]
       LANDING_PREFIX= os.environ.get("LANDING_PREFIX", "landing/")
       DATASET       = os.environ["DATASET"]

       def _fetch():
           # build URL + headers + auth from env, do a GET, return bytes
           ...

       def lambda_handler(event, context):
           body = _fetch()
           now = datetime.now(timezone.utc)
           key = (
               f"{LANDING_PREFIX}{DATASET}/ingest_date={now:%Y-%m-%d}/"
               f"hour={now:%H}/{DATASET}_{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex}.json"
           )
           S3.put_object(Bucket=DATA_BUCKET, Key=key, Body=body,
                         ContentType="application/json",
                         ServerSideEncryption="AES256")
           return {"ok": True, "key": key, "bytes": len(body)}

     Rules for the generated code:
       * Use ONLY the standard library + boto3 (no `requests`, no
         `urllib3`).
       * Read the credential at RUNTIME via boto3 SecretsManager when
         API_SECRET_NAME is set in env. Never hard-code secrets.
       * Honor the `auth_type` you saw in step 1 (or the inline spec):
           - "none"            -> NO auth, NO Secrets Manager call
           - "bearer"          -> Authorization: Bearer <token>
           - "api_key_header"  -> <header_name>: <key>
           - "api_key_query"   -> append ?<query_param>=<key>
           - "basic"           -> base64-encoded user:password
       * If a `default_path`/`default_query` is configured, include it.
       * If pagination metadata exists, loop. Stop after 10 pages by
         default to keep the test deterministic.
       * Always wrap the HTTP call in try/except urllib.error.HTTPError
         and surface response.status + first 500 bytes of body in the
         exception so CloudWatch logs are useful.
       * Output is a single JSON document (or NDJSON if the upstream
         returned a streamable list) per invocation, encoded UTF-8.

  3. write_lambda_code(dataset, code). Use the codeUri it returns.

  4. deploy_lambda(dataset, code_uri, secret_name?, schedule, env?).
       * `schedule` defaults to "rate(1 hour)"; pick a sensible cadence
         based on the dataset (heavy data -> "rate(6 hours)", small ref
         data -> "rate(15 minutes)"; never faster than 5 minutes).
       * `secret_name` MUST be omitted (pass "") when there is no auth.
       * `env` is a JSON-encoded object of extra runtime env vars. Use
         it to pass API_ENDPOINT, AUTH_TYPE, HEADER_NAME, QUERY_PARAM,
         DEFAULT_PATH, DEFAULT_QUERY, PAGINATION_MODE, etc. Do NOT
         include credentials.

  5. invoke_lambda_test(dataset). Confirm `ok: true`,
     `objectCount >= 1`, and capture the first landed key.

  6. write_integration_report(dataset, plan). The report JSON MUST
     have these top-level keys:
       - dataset
       - source: { endpoint, authType, secretName? }
       - lambda: { functionName, functionArn }
       - schedule: { ruleName, ruleArn, expression }
       - landing: { bucket, prefix, sampleKey, objectCount }
       - testRun: { statusCode, ok, responseBody (truncated <= 1KB) }
       - codeUri
       - reportUri (will be set by the tool itself; reflect it back)
       - status: "active" | "deployed-untested" | "failed"
       - notes

  7. Return ONE JSON object — the same report — as your final message.
     The message must START with `{` and END with `}`. No commentary.

Never invent values not present in tool outputs. If a tool returns
`ok: false`, capture the error in `notes` and emit a report with
`status: "failed"` rather than retrying blindly more than once.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            list_api_secrets,
            read_api_secret,
            write_lambda_code,
            deploy_lambda,
            invoke_lambda_test,
            write_integration_report,
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
    dataset = payload.get("dataset")
    if not dataset:
        return json.dumps({"error": "missing 'dataset' in payload"})
    secret_name = payload.get("secret_name")
    api_spec = payload.get("api_spec")
    description = payload.get("description") or ""
    schedule = payload.get("schedule") or "rate(1 hour)"

    request = {
        "dataset": dataset,
        "secret_name": secret_name,
        "api_spec": api_spec,
        "description": description,
        "schedule": schedule,
        "landingPrefix": f"s3://{REPORT_BUCKET}/{LANDING_PREFIX}{dataset}/",
    }
    user_prompt = (
        "Build, deploy, and live-test an API integration with the following "
        "request. Follow your standard workflow exactly. PUBLIC APIs with no "
        "authentication are fine — pass `secret_name=\"\"` to deploy_lambda "
        "in that case.\n\n"
        f"REQUEST:\n{json.dumps(request, indent=2)}"
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
