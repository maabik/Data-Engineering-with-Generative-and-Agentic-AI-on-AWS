"""Daily Bedrock Evaluations batch layer.

Runs once a day (EventBridge schedule). For each agent in the registry:

  1. Concatenates yesterday's BYOI rows from
       s3://${VERDICTS_BUCKET}/dataset/agent_name=<name>/dt=<yesterday>/
     into a single JSONL file at
       s3://${VERDICTS_BUCKET}/eval-input/agent_name=<name>/dt=<yesterday>.jsonl
  2. Calls bedrock CreateEvaluationJob with:
       - applicationType=ModelEvaluation
       - inferenceConfig.models[].precomputedInferenceSource (BYOI — uses
         the captured prompt/response pairs as-is, no inference re-run)
       - automated.datasetMetricConfigs.{taskType=General, ...}
       - evaluatorModelConfig pointing at Claude Sonnet
  3. The job appears on the Bedrock console's Evaluations page with the
     agent's name embedded in the job name so they're easy to filter.
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

REGION = os.environ["AWS_REGION"]
VERDICTS_BUCKET = os.environ["VERDICTS_BUCKET"]
EVAL_ROLE_ARN = os.environ["EVAL_ROLE_ARN"]
JUDGE_MODEL_ID = os.environ.get(
    "JUDGE_MODEL_ID",
    f"arn:aws:bedrock:{REGION}::inference-profile/us.anthropic.claude-sonnet-4-6",
)
REGISTRY_KEY = os.environ.get("REGISTRY_KEY", "registry.json")
MIN_ROWS = int(os.environ.get("MIN_ROWS_PER_JOB", "1"))

# Bedrock LLM-as-judge metrics for ModelEvaluation jobs.
DEFAULT_METRICS = ["Builtin.Correctness", "Builtin.Helpfulness", "Builtin.Coherence"]

log = logging.getLogger()
log.setLevel(logging.INFO)

s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock", region_name=REGION)


def lambda_handler(event: dict, _context) -> dict:
    target_date = _resolve_date(event)
    log.info("running batch eval for date=%s", target_date)

    registry = _load_registry()
    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for agent in registry.get("agents", []):
        # Bedrock Evaluations runs in this Lambda's region; only submit the
        # us-east-1 agents from the same registry. (us-west-2 agents are still
        # captured by the online layer; their batch jobs would need a sister
        # Lambda in us-west-2.)
        if agent["region"] != REGION:
            skipped.append({"name": agent["name"], "reason": f"region={agent['region']} != {REGION}"})
            continue

        try:
            input_uri, row_count = _build_input(agent["name"], target_date)
        except Exception as e:  # noqa: BLE001
            log.exception("build_input failed for %s: %s", agent["name"], e)
            skipped.append({"name": agent["name"], "reason": f"build_input_error: {e}"})
            continue

        if row_count < MIN_ROWS:
            skipped.append({"name": agent["name"], "reason": f"only {row_count} rows captured"})
            continue

        try:
            job_arn = _create_eval_job(agent, input_uri, target_date)
            submitted.append({"name": agent["name"], "rows": row_count, "job_arn": job_arn})
        except Exception as e:  # noqa: BLE001
            log.exception("create_evaluation_job failed for %s: %s", agent["name"], e)
            skipped.append({"name": agent["name"], "reason": f"create_job_error: {e}"})

    return {"submitted": submitted, "skipped": skipped, "date": target_date}


def _resolve_date(event: dict) -> str:
    """Allow override via event['date'] = 'YYYY-MM-DD'; default = yesterday UTC."""
    if event and isinstance(event, dict) and event.get("date"):
        return str(event["date"])
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _load_registry() -> dict[str, Any]:
    obj = s3.get_object(Bucket=VERDICTS_BUCKET, Key=REGISTRY_KEY)
    return json.loads(obj["Body"].read())


def _build_input(agent_name: str, dt: str) -> tuple[str, int]:
    """Concatenate per-request BYOI rows for one (agent, date) into a single file.

    Returns (input_s3_uri, row_count).

    Note: Bedrock CreateEvaluationJob's S3 URI validator rejects keys
    containing `=`, so partition-style `agent_name=…` paths only work for
    capture (verdicts/dataset). The eval-input/eval-output paths use a
    sanitized layout the validator accepts.
    """
    src_prefix = f"dataset/agent_name={agent_name}/dt={dt}/"
    safe_agent = agent_name.replace("_", "-").replace(".", "-")
    dst_key = f"eval-input/{safe_agent}/{dt}.jsonl"

    buf = io.BytesIO()
    rows = 0
    token = None
    while True:
        kw: dict[str, Any] = {"Bucket": VERDICTS_BUCKET, "Prefix": src_prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents", []):
            body = s3.get_object(Bucket=VERDICTS_BUCKET, Key=obj["Key"])["Body"].read()
            if not body.endswith(b"\n"):
                body += b"\n"
            buf.write(body)
            rows += 1
        token = resp.get("NextContinuationToken")
        if not token:
            break

    if rows == 0:
        return f"s3://{VERDICTS_BUCKET}/{dst_key}", 0

    s3.put_object(
        Bucket=VERDICTS_BUCKET,
        Key=dst_key,
        Body=buf.getvalue(),
        ContentType="application/x-jsonlines",
    )
    return f"s3://{VERDICTS_BUCKET}/{dst_key}", rows


def _create_eval_job(agent: dict[str, Any], input_uri: str, dt: str) -> str:
    job_name = _job_name(agent["name"], dt)
    safe_agent = agent["name"].replace("_", "-").replace(".", "-")
    output_uri = f"s3://{VERDICTS_BUCKET}/eval-output/{safe_agent}/{dt}/"

    eval_config = {
        "automated": {
            "datasetMetricConfigs": [{
                "taskType": "General",
                "dataset": {
                    "name": agent["name"],
                    "datasetLocation": {"s3Uri": input_uri},
                },
                "metricNames": DEFAULT_METRICS,
            }],
            "evaluatorModelConfig": {
                "bedrockEvaluatorModels": [{"modelIdentifier": JUDGE_MODEL_ID}],
            },
        }
    }

    inference_config = {
        "models": [{
            "precomputedInferenceSource": {"inferenceSourceIdentifier": agent["name"]},
        }],
    }

    output_data_config = {"s3Uri": output_uri}

    resp = bedrock.create_evaluation_job(
        jobName=job_name,
        roleArn=EVAL_ROLE_ARN,
        applicationType="ModelEvaluation",
        evaluationConfig=eval_config,
        inferenceConfig=inference_config,
        outputDataConfig=output_data_config,
    )
    return resp["jobArn"]


def _job_name(agent_name: str, dt: str) -> str:
    """Bedrock job names cap at ~63 chars and must be unique per region.

    Pattern: oae-<agent>-<date>-<epoch>. Underscores in agent names → hyphens
    since the API only accepts [a-zA-Z0-9-]. The epoch suffix prevents
    collisions when the batch is re-run for the same agent/date (manual
    trigger, retry, or backfill).
    """
    import time
    safe = agent_name.replace("_", "-").replace(".", "-")[:35]
    epoch = int(time.time())
    return f"oae-{safe}-{dt}-{epoch}"
