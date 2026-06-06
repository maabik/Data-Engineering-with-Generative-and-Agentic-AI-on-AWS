"""log-analysis-agent — Strands agent on AgentCore Runtime that turns
a pipeline-run descriptor (Airflow + Spark) into a structured analysis
of what the logs say happened, persists it to S3, and notifies via SNS.

Workflow:
    1. read_pipeline_run(run_uri)         -> pipeline metadata + log pointers
    2. read_airflow_logs(...)             -> tail Airflow scheduler/task log
    3. read_spark_logs(...)               -> tail Spark driver/executor log
    4. list_pipeline_history(pipeline)    -> chronic vs first-time?
    5. write_log_insights(run_uri, ...)
    6. send_sns_notification(...)
    7. Emit ONE JSON object: the insights, with planUri == insightsUri.
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
def read_pipeline_run(run_uri: str) -> str:
    """Fetch the pipeline-run descriptor JSON (s3://.../pipeline-runs/<run_id>.json).
    Returns {ok, runUri, run:{run_id,pipeline,status,started_at,ended_at,airflow,spark}}."""
    return json.dumps(_sync(_gateway_call(_ns("read_pipeline_run"), {"run_uri": run_uri})), indent=2)


@tool
def read_airflow_logs(source: dict, max_lines: int = 200) -> str:
    """Tail the Airflow log for this run. `source` is one of:
    {"s3_uri":"s3://bucket/path/to/log_or_prefix"} or
    {"log_group":"/aws/airflow/...","log_stream":"<task_log_stream>"}.
    Returns at most `max_lines` (capped at 1000) most-recent log lines."""
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("read_airflow_logs"),
                {"source": source, "max_lines": max_lines},
            )
        ),
        indent=2,
    )


@tool
def read_spark_logs(source: dict, max_lines: int = 200) -> str:
    """Tail the Spark driver/executor stderr for this run. Same shape as
    read_airflow_logs but typically points at the EMR/EMR-Serverless
    `applications/.../SPARK_DRIVER/stderr.gz` prefix or the EMR-on-EKS
    `/aws/emr-containers/jobs/<id>` log group."""
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("read_spark_logs"),
                {"source": source, "max_lines": max_lines},
            )
        ),
        indent=2,
    )


@tool
def list_pipeline_history(pipeline: str, limit: int = 10) -> str:
    """List the most-recent pipeline-run records for `pipeline` from
    s3://${BUCKET}/pipeline-runs/. Use to decide whether the failure
    is first-time or recurring."""
    return json.dumps(
        _sync(_gateway_call(_ns("list_pipeline_history"), {"pipeline": pipeline, "limit": limit})),
        indent=2,
    )


@tool
def write_log_insights(run_uri: str, insights: str) -> str:
    """Persist the agent's final insights JSON to
    s3://${BUCKET}/log-insights/<run_id>.json. Returns {ok, insightsUri}."""
    parsed: Any = insights
    if isinstance(insights, str):
        try:
            parsed = json.loads(insights)
        except json.JSONDecodeError:
            parsed = insights
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("write_log_insights"),
                {"run_uri": run_uri, "insights": parsed},
            )
        ),
        indent=2,
    )


@tool
def send_sns_notification(subject: str, message: str) -> str:
    """Publish to the configured SNS topic. `message` should be a short
    headline (1-3 sentences); include the insightsUri so on-call can
    pull the full plan from S3."""
    return json.dumps(
        _sync(
            _gateway_call(
                _ns("send_sns_notification"),
                {"subject": subject, "message": message},
            )
        ),
        indent=2,
    )


SYSTEM_PROMPT = """You are the Pipeline Log Analysis Agent. You sit
downstream of a data pipeline orchestrator (Airflow / Step Functions /
EMR step) and are triggered by EventBridge when a pipeline run
descriptor lands at s3://${BUCKET}/pipeline-runs/<run_id>.json. Your
job is to read the run descriptor + the Airflow and Spark logs it
points to, decide what happened, persist a structured analysis to S3,
and send a one-line SNS notification.

Workflow (follow in order):

  1. read_pipeline_run(run_uri). Pull out: run_id, pipeline, status,
     started_at, ended_at, airflow:{...}, spark:{...}. The `airflow`
     and `spark` blocks each contain a `source` shaped like
     `{"s3_uri": "..."}` or `{"log_group": "...", "log_stream": "..."}`.

  2. read_airflow_logs(source=run.airflow.source, max_lines=300). Look
     for: task failures (`Task exited with return code`), retries,
     SLA misses, scheduler errors, DAG import errors, sensor timeouts,
     and the final task state. Capture the first ERROR/Traceback you
     see verbatim under `evidence`.

  3. read_spark_logs(source=run.spark.source, max_lines=300). Look
     for: `org.apache.spark.SparkException`, OOM
     (`java.lang.OutOfMemoryError`), executor lost, shuffle fetch
     failures, schema mismatches (`AnalysisException`), file not
     found, S3 throttling (503/SlowDown), Hive metastore errors. Pull
     the actual exception class + first few stack frames into
     `evidence`. If status was Success and the spark log is clean,
     note the runtime and shuffle volume if visible.

  4. list_pipeline_history(pipeline=run.pipeline, limit=10). Decide
     if this is regression (first failure in 10) or chronic
     (>=2 of last 5 failed).

  5. write_log_insights(run_uri, <insights_json>). The insights_json
     MUST have these top-level keys:
       - run_id, pipeline, status   (echoed from the run descriptor)
       - verdict: one of "ok", "investigate", "broken"
       - severity: "info" | "low" | "medium" | "high" | "critical"
       - rootCauses: array of {{ id, component: "airflow|spark|infra",
           hypothesis, evidence (1-3 actual log excerpts, each <=400
           chars), confidence: "low|medium|high" }}
       - timeline: short array of {{ ts, component, event }} entries
           assembled from the log excerpts (start, first error, end)
       - recommendations: array of {{ priority, owner, action, detail,
           estimatedEffort }} ordered by priority (1 = first)
       - history: {{ recentRuns: <count>, failureRate: <0..1>,
           trend: "improving|stable|degrading|first-failure" }}
       - summary: one-paragraph executive summary (<=600 chars)

  6. send_sns_notification(subject=<short subject>, message=<full report>).
     Subject: `[<verdict>] <pipeline> run <run_id>` (capped at 100 chars).
     Message: a multi-section plain-text report formatted EXACTLY like
     this template (use real newlines, not the literal string \n):

         Pipeline: <pipeline>
         Run ID:   <run_id>
         Status:   <status>  (verdict: <verdict>, severity: <severity>)
         Window:   <started_at> -> <ended_at>

         SUMMARY
         <one-paragraph executive summary from the insights>

         ROOT CAUSES
         1. [<component>] <hypothesis> (confidence: <c>)
            Evidence:
              - <evidence line 1>
              - <evidence line 2>
         2. ...

         TIMELINE
         <ts> [<component>] <event>
         ...

         RECOMMENDATIONS
         1. (P<priority>, <owner>, ~<effort>) <action>
            <detail>
         2. ...

         HISTORY
         recentRuns=<n>  failureRate=<r>  trend=<t>

         Insights JSON: <insightsUri>

     Include EVERY rootCause, EVERY timeline entry, and EVERY
     recommendation from the insights JSON — do not truncate. Plain
     text only (no markdown fences, no JSON).

  7. Return ONE JSON object (and nothing else outside it) — the same
     insights you wrote, with an extra `insightsUri` field set to the
     URI write_log_insights returned.

Rules:
  * Never invent log lines, exception classes, or stack frames not in
    the tool outputs. If a log fetch fails (ok:false), record the
    error under `evidence` and continue with what you have.
  * If status is `Success` and both logs are clean, set verdict =
    "ok", severity = "info", rootCauses = [], recommendations = []
    (or one low-priority "monitor" step), and write a short summary
    saying the run completed cleanly.
  * If status is `Failed`/`Error` AND no clear root cause is visible
    in either log, set verdict = "investigate", severity = "medium",
    and recommend the engineer pull a longer log window.
  * Your FINAL message must START with `{` and END with `}`. No
    preamble, no markdown fences, no commentary outside the JSON
    object.
"""


def _make_agent() -> Agent:
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        tools=[
            read_pipeline_run,
            read_airflow_logs,
            read_spark_logs,
            list_pipeline_history,
            write_log_insights,
            send_sns_notification,
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
    run_uri = payload.get("run_uri") or payload.get("prompt", "")
    if not run_uri:
        return json.dumps({"error": "missing run_uri in payload"})
    user_prompt = (
        f"A new pipeline-run descriptor has landed at {run_uri}. Produce "
        f"a log analysis report per your instructions and notify on SNS."
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
