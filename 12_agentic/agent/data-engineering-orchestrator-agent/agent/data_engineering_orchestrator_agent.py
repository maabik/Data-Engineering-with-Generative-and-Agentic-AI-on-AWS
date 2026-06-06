"""Data Engineering Orchestrator Agent.

A Strands agent on Amazon Bedrock AgentCore Runtime that fans out to all
specialist data engineering agents shipped in this repo. The orchestrator
inspects an incoming natural-language request, selects one or more
downstream agents, invokes them (in parallel where useful), and returns a
consolidated response.

Each downstream agent is itself an AgentCore Runtime hosted in this AWS
account. The orchestrator is given the registry of `{name: {arn, region,
description, payload_hint}}` at deploy time via the `AGENT_REGISTRY_JSON`
environment variable. Each registry entry is exposed to the LLM as a
single Strands `@tool` named `invoke_<name>`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
import urllib.request
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
REGISTRY_JSON = os.environ.get("AGENT_REGISTRY_JSON", "{}")

try:
    AGENT_REGISTRY: dict[str, dict[str, Any]] = json.loads(REGISTRY_JSON)
except json.JSONDecodeError:
    AGENT_REGISTRY = {}

_runtime_clients: dict[str, Any] = {}
_secrets_clients: dict[str, Any] = {}
_cognito_clients: dict[str, Any] = {}
_token_cache: dict[str, str] = {}  # secret_name -> access_token


def _runtime_client(region: str):
    if region not in _runtime_clients:
        from botocore.config import Config
        # AgentCore Runtime supports up to 8 hours per invocation
        # (28,800 s). Sit a hair under that so we surface AgentCore's
        # own timeout error rather than a botocore one.
        cfg = Config(read_timeout=28_700, connect_timeout=10, retries={"max_attempts": 1})
        _runtime_clients[region] = boto3.client(
            "bedrock-agentcore", region_name=region, config=cfg
        )
    return _runtime_clients[region]


def _secrets_client(region: str):
    if region not in _secrets_clients:
        _secrets_clients[region] = boto3.client("secretsmanager", region_name=region)
    return _secrets_clients[region]


def _cognito_client(region: str):
    if region not in _cognito_clients:
        _cognito_clients[region] = boto3.client("cognito-idp", region_name=region)
    return _cognito_clients[region]


def _bearer_token(secret_name: str, region: str) -> str:
    """Fetch a Cognito access token using credentials from Secrets Manager."""
    if secret_name in _token_cache:
        return _token_cache[secret_name]
    secret = _secrets_client(region).get_secret_value(SecretId=secret_name)
    creds = json.loads(secret["SecretString"])
    auth = _cognito_client(region).initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    token = auth["AuthenticationResult"]["AccessToken"]
    _token_cache[secret_name] = token
    return token


def _invoke_with_jwt(arn: str, region: str, secret_name: str, payload: dict) -> dict:
    """Invoke an AgentCore Runtime that requires a Cognito Bearer token."""
    token = _bearer_token(secret_name, region)
    encoded = urllib.parse.quote(arn, safe="")
    url = (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded}"
        f"/invocations?qualifier=DEFAULT"
    )
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=28_700) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {text[:500]}"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _ensure_non_empty_payload(payload: dict) -> dict:
    """Specialist agents typically pass `payload['prompt']` straight to
    Bedrock's Converse API. An empty string is rejected as a
    ValidationException and surfaces as a runtime 500. Backfill `prompt`
    from the original user request if the LLM forgot to include it."""
    out = dict(payload or {})
    if not (out.get("prompt") or "").strip():
        text_keys = ("request", "input_uri", "s3_uri", "run_uri", "design_uri",
                     "insights_uri", "dataset", "description", "sql")
        for k in text_keys:
            v = out.get(k)
            if isinstance(v, str) and v.strip():
                out["prompt"] = v
                break
        else:
            out["prompt"] = _CURRENT_PROMPT[0] or "ping"
    return out


def _invoke_runtime(arn: str, region: str, payload: dict, auth_secret: str | None = None) -> dict:
    """Invoke an AgentCore Runtime. If auth_secret is provided, use JWT
    Bearer auth (Cognito); otherwise use SigV4 (the runtime's IAM auth)."""
    payload = _ensure_non_empty_payload(payload)
    if auth_secret:
        return _invoke_with_jwt(arn, region, auth_secret, payload)
    client = _runtime_client(region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = response["response"].read().decode("utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


@tool
def list_agents() -> dict:
    """List every specialist data engineering agent the orchestrator can route
    to, with a one-line description and the payload shape each one expects."""
    return {
        name: {
            "description": meta.get("description", ""),
            "region": meta.get("region", DEFAULT_REGION),
            "payload_hint": meta.get("payload_hint", {}),
        }
        for name, meta in AGENT_REGISTRY.items()
    }


@tool
def invoke_agent(name: str, payload: dict) -> dict:
    """Invoke a single named specialist agent with the given payload.
    Returns the agent's structured response (or a `raw` field if the agent
    did not return JSON). Use `list_agents` first to find the right name."""
    meta = AGENT_REGISTRY.get(name)
    if not meta:
        return {
            "error": f"unknown agent '{name}'",
            "available": sorted(AGENT_REGISTRY.keys()),
        }
    region = meta.get("region", DEFAULT_REGION)
    arn = meta["arn"]
    auth_secret = meta.get("auth_secret")
    return _invoke_runtime(arn, region, payload, auth_secret=auth_secret)


@tool
def get_glue_job_run(job_name: str, run_id: str, region: str = "us-west-2") -> dict:
    """Check the status of a Glue job run (DataBrew profile jobs and Glue
    ETL/Deequ jobs both expose this). Returns a small dict with
    JobRunState (RUNNING|SUCCEEDED|FAILED|TIMEOUT|STOPPED) and timing.
    Use this to poll long-running profiling/quality work without
    re-invoking the specialist agent."""
    try:
        glue = boto3.client("glue", region_name=region)
        run = glue.get_job_run(JobName=job_name, RunId=run_id)["JobRun"]
        return {
            "JobRunState": run.get("JobRunState"),
            "StartedOn": str(run.get("StartedOn")),
            "CompletedOn": str(run.get("CompletedOn")) if run.get("CompletedOn") else None,
            "ExecutionTime": run.get("ExecutionTime"),
            "ErrorMessage": run.get("ErrorMessage"),
        }
    except Exception as exc:
        # Fall back to DataBrew if it's a DataBrew job, not a Glue job.
        try:
            db = boto3.client("databrew", region_name=region)
            r = db.describe_job_run(Name=job_name, RunId=run_id)
            return {
                "State": r.get("State"),
                "StartedOn": str(r.get("StartedOn")),
                "CompletedOn": str(r.get("CompletedOn")) if r.get("CompletedOn") else None,
                "ExecutionTime": r.get("ExecutionTime"),
                "LogSubscription": r.get("LogSubscription"),
                "ErrorMessage": r.get("ErrorMessage"),
            }
        except Exception as exc2:
            return {"error": f"glue: {exc} | databrew: {exc2}"}


@tool
def get_step_function_execution(execution_arn: str, region: str = "us-east-1") -> dict:
    """Check the status of a Step Functions execution started by text_to_dag_agent
    (or any other agent). Returns status, output, and any error cause."""
    try:
        sfn = boto3.client("stepfunctions", region_name=region)
        e = sfn.describe_execution(executionArn=execution_arn)
        return {
            "status": e.get("status"),
            "startDate": str(e.get("startDate")),
            "stopDate": str(e.get("stopDate")) if e.get("stopDate") else None,
            "output": e.get("output"),
            "error": e.get("error"),
            "cause": e.get("cause"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@tool
def list_recent_agent_logs(
    agent_name: str, minutes_back: int = 10, region: str = "us-west-2", limit: int = 30
) -> dict:
    """Read the most recent CloudWatch log events for an AgentCore
    Runtime by agent name (e.g. 'data_profiling_agent'). Useful when an invocation
    timed out or returned an opaque error and you need to see what the
    agent was doing. Returns up to `limit` log events from the last
    `minutes_back` minutes across all log streams in the agent's log
    group."""
    import time as _time
    end = int(_time.time() * 1000)
    start = end - (minutes_back * 60_000)
    logs = boto3.client("logs", region_name=region)
    # AgentCore log groups are named "<agent_name>-<runtime_suffix>-DEFAULT".
    # Resolve the right group by prefix match so callers don't have to know
    # the runtime suffix.
    try:
        groups = logs.describe_log_groups(
            logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{agent_name}"
        ).get("logGroups", [])
        if not groups:
            return {"error": f"no log group for {agent_name}"}
        log_group = groups[0]["logGroupName"]
        resp = logs.filter_log_events(
            logGroupName=log_group, startTime=start, endTime=end, limit=limit
        )
        events = [
            {"ts": str(e.get("timestamp")), "msg": (e.get("message") or "")[:500]}
            for e in resp.get("events", [])
        ]
        return {"log_group": log_group, "events": events, "count": len(events)}
    except Exception as exc:
        return {"error": str(exc)}


@tool
def invoke_agents_parallel(invocations: list[dict]) -> dict:
    """Invoke several specialist agents concurrently and return a dict keyed
    by their names. `invocations` is a list of `{name, payload}` items.
    Use this when a request requires fanning out to multiple agents whose
    work is independent (e.g. profile + design + quality on different inputs).
    """
    results: dict[str, Any] = {}
    if not invocations:
        return results

    def _run(item: dict) -> tuple[str, dict]:
        name = item.get("name") or ""
        payload = item.get("payload") or {}
        meta = AGENT_REGISTRY.get(name)
        if not meta:
            return name, {"error": f"unknown agent '{name}'"}
        try:
            return name, _invoke_runtime(
                meta["arn"],
                meta.get("region", DEFAULT_REGION),
                payload,
                auth_secret=meta.get("auth_secret"),
            )
        except Exception as exc:
            return name, {"error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(8, len(invocations))) as pool:
        futures = [pool.submit(_run, item) for item in invocations]
        for fut in as_completed(futures):
            name, result = fut.result()
            results[name] = result
    return results


def _registry_summary() -> str:
    if not AGENT_REGISTRY:
        return "(no downstream agents configured — AGENT_REGISTRY_JSON is empty)"
    lines = []
    for name, meta in AGENT_REGISTRY.items():
        desc = meta.get("description", "")
        lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You are the Data Engineering Orchestrator. Every data
engineering request to this organization comes through you. You DO NOT do
the work yourself — you route it to the right specialist agent(s) and
combine their answers.

## ROUTING RULE (mandatory)

If ANY specialist agent below could plausibly handle the user's request,
you MUST call it via `invoke_agent` (or `invoke_agents_parallel`). You
must NOT produce the deliverable yourself, even if you think you could.
Your value is routing + synthesis — not authoring SQL, DAGs, schemas,
Lambdas, or analyses directly. If a specialist returns an error, capture
that error verbatim in `results` and surface it in `summary`. Do not
substitute your own answer for a failed agent's answer.

The ONLY case where you do not invoke any specialist is when no agent in
the registry is even a partial fit for the request — in which case
`agents_invoked` is `[]` and `summary` explains plainly that there is no
matching specialist.

## Specialist agents you can call

{_registry_summary()}

## How to choose

1. Call `list_agents` if you need the full payload shape of a candidate
   agent. Most of the time the names alone are enough.
2. Pick the smallest set of agents that fully answers the request:
   - Single-shot questions → one agent via `invoke_agent`.
   - Multi-step pipelines (profile → design → quality) → call them in
     sequence with `invoke_agent`, feeding each agent's output into the
     next agent's payload as appropriate.
   - Independent questions that share a request (e.g. "review this SQL
     AND describe the lineage") → use `invoke_agents_parallel`.
3. Be explicit about what you sent to each agent and what it returned.
4. If no agent is a clear fit, say so plainly. Do not invent capabilities.

## Polling long-running work

AgentCore Runtime invocations are synchronous and capped at 8 hours.
When a specialist kicks off a long-running AWS job (Glue, DataBrew
profile, Step Functions, Spark/Deequ) you may want to poll the
underlying job rather than re-invoking the agent. Use:

- `get_glue_job_run(job_name, run_id, region)` — Glue / DataBrew jobs.
  The specialist usually returns the job_name + run_id in its response.
- `get_step_function_execution(execution_arn)` — Step Functions started
  by text_to_dag_agent.
- `list_recent_agent_logs(agent_name, minutes_back)` — CloudWatch logs
  for any agent runtime when an invocation timed out or returned an
  opaque error.

Do NOT poll on a tight loop; one or two checks at most, then return.

## Output

Return a single JSON object on your final assistant turn with this shape:

```json
{{
  "plan": "1-3 sentence description of the routing decision",
  "agents_invoked": ["name1", "name2"],
  "results": {{ "name1": <agent response>, "name2": <agent response> }},
  "summary": "user-facing answer that synthesizes the agent results"
}}
```

No prose outside the JSON. No markdown fences.
"""


app = BedrockAgentCoreApp()


def create_agent() -> Agent:
    model = BedrockModel(model_id=MODEL_ID, region_name=DEFAULT_REGION)
    return Agent(
        model=model,
        tools=[
            list_agents,
            invoke_agent,
            invoke_agents_parallel,
            get_glue_job_run,
            get_step_function_execution,
            list_recent_agent_logs,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def _extract_json(text: str) -> dict:
    start = text.find("{")
    if start == -1:
        return {"summary": text, "agents_invoked": [], "results": {}}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return {"summary": text, "agents_invoked": [], "results": {}}
    return {"summary": text, "agents_invoked": [], "results": {}}


_CURRENT_PROMPT: list[str] = [""]  # one-element holder, threaded via Strands tools


@app.entrypoint
def invoke(payload):
    prompt = payload.get("prompt") or payload.get("request") or ""
    if not prompt.strip():
        return {
            "plan": "no input",
            "agents_invoked": [],
            "results": {},
            "summary": "Send a `prompt` field describing the data engineering task.",
        }
    _CURRENT_PROMPT[0] = prompt
    agent = create_agent()
    response = agent(prompt)
    text = response.message["content"][-1].get("text", "")
    return _extract_json(text)


if __name__ == "__main__":
    app.run()
