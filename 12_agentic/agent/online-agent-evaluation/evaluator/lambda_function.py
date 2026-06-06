"""Online Agent Evaluation — CloudWatch Logs subscription handler.

Receives a base64+gzip batch of log events from a Bedrock agent (AgentCore
Runtime or classic Bedrock Agent), samples a configurable fraction, asks
Claude Sonnet to judge each sampled (input, output) pair, and writes:

  - CloudWatch metrics under namespace OnlineAgentEvaluation
  - structured CloudWatch log lines (this Lambda's own log group)
  - JSONL records to s3://${VERDICTS_BUCKET}/verdicts/agent_name=…/dt=…/

Per-agent sample rate overrides live in the DynamoDB table named by
SAMPLE_RATE_TABLE; everything else falls back to SAMPLE_RATE.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from judge_prompt import SYSTEM_PROMPT, build_user_prompt

# How long to remember a partial trace. AgentCore traces typically resolve in
# seconds; 1h is generous and TTL-cleans abandoned traces automatically.
TRACE_TTL_SECONDS = 3600

# ─── Config ─────────────────────────────────────────────────────────────────
REGION = os.environ["AWS_REGION"]
SAMPLE_RATE = float(os.environ.get("SAMPLE_RATE", "0.05"))
JUDGE_MODEL_ID = os.environ.get("JUDGE_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
VERDICTS_BUCKET = os.environ["VERDICTS_BUCKET"]
SAMPLE_RATE_TABLE = os.environ.get("SAMPLE_RATE_TABLE", "")
TRACES_TABLE = os.environ.get("TRACES_TABLE", "")
REGISTRY_KEY = os.environ.get("REGISTRY_KEY", "registry.json")
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "OnlineAgentEvaluation")

# ─── Clients ────────────────────────────────────────────────────────────────
log = logging.getLogger()
log.setLevel(logging.INFO)

bedrock = boto3.client("bedrock-runtime", region_name=REGION)
cloudwatch = boto3.client("cloudwatch", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION) if SAMPLE_RATE_TABLE else None
traces_table = ddb.Table(TRACES_TABLE) if (ddb and TRACES_TABLE) else None

_REGISTRY_CACHE: dict[str, Any] = {"data": None, "loaded_at": 0.0}
_REGISTRY_TTL_SECONDS = 300


def lambda_handler(event: dict, _context) -> dict:
    """Entry point — CloudWatch Logs subscription delivers a single payload."""
    payload = event.get("awslogs", {}).get("data")
    if not payload:
        log.warning("no awslogs payload, ignoring event")
        return {"status": "skipped"}

    decoded = json.loads(gzip.decompress(base64.b64decode(payload)))
    log_group = decoded.get("logGroup", "")
    agent_meta = _resolve_agent(log_group)
    if not agent_meta:
        log.info("log group %s not in registry; skipping", log_group)
        return {"status": "unknown_agent"}

    agent_name = agent_meta["name"]
    agent_type = agent_meta["type"]
    rate = _sample_rate_for(agent_name)

    records = _extract_records(decoded.get("logEvents", []), agent_name)
    judged = 0
    for record in records:
        if not _should_sample(record["request_id"], rate):
            continue
        try:
            verdict = _judge(agent_meta, record)
        except Exception as e:  # noqa: BLE001 — never let one bad log kill the batch
            log.exception("judge failed for %s/%s: %s", agent_name, record["request_id"], e)
            continue
        _emit(agent_name, agent_type, record, verdict)
        judged += 1

    log.info(
        "agent=%s log_group=%s events=%d records=%d sampled=%d rate=%.3f",
        agent_name, log_group, len(decoded.get("logEvents", [])), len(records), judged, rate,
    )
    return {"status": "ok", "records": len(records), "judged": judged}


# ─── Registry resolution (log group → agent metadata) ───────────────────────


def _registry() -> dict[str, Any]:
    now = time.time()
    if _REGISTRY_CACHE["data"] and now - _REGISTRY_CACHE["loaded_at"] < _REGISTRY_TTL_SECONDS:
        return _REGISTRY_CACHE["data"]
    obj = s3.get_object(Bucket=VERDICTS_BUCKET, Key=REGISTRY_KEY)
    data = json.loads(obj["Body"].read())
    _REGISTRY_CACHE["data"] = data
    _REGISTRY_CACHE["loaded_at"] = now
    return data


def _resolve_agent(log_group: str) -> Optional[dict[str, Any]]:
    reg = _registry()
    return reg.get("by_log_group", {}).get(log_group)


# ─── Sampling ───────────────────────────────────────────────────────────────


def _sample_rate_for(agent_name: str) -> float:
    if not ddb:
        return SAMPLE_RATE
    try:
        table = ddb.Table(SAMPLE_RATE_TABLE)
        item = table.get_item(Key={"agent_name": agent_name}).get("Item")
        if item and "sample_rate" in item:
            return float(item["sample_rate"])
    except ClientError as e:
        log.warning("rate lookup failed for %s: %s", agent_name, e)
    return SAMPLE_RATE


def _should_sample(request_id: str, rate: float) -> bool:
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    # Deterministic hash so the same request never gets two different decisions
    # if a log batch is reprocessed (e.g. retry) — and so the sample is roughly
    # uniform without per-Lambda-instance randomness drift.
    h = int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16)
    return (h / 0xFFFFFFFF) < rate


# ─── Log event parsing ──────────────────────────────────────────────────────


def _extract_records(raw_events: list[dict[str, Any]], agent_name: str) -> list[dict[str, Any]]:
    """Extract (request_id, prompt, response) tuples from a log batch.

    Two-stage stitching:

    1. **In-memory** — group events in this batch by traceId. Short traces
       finish here without touching DDB.
    2. **DDB-backed (oae-traces)** — for traces where only the prompt or only
       the response landed in this batch, store/retrieve the missing half via
       DynamoDB. Rows have a TTL so abandoned traces auto-clean.

    Supports a flat shape too (`{requestId, input, output}` on one line) for
    agents that emit a single bundled record.
    """
    by_trace: dict[str, dict[str, Any]] = {}
    flat_records: list[dict[str, Any]] = []

    for raw in raw_events:
        msg = raw.get("message", "")
        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        # Flat shape: a single line with both fields
        if "input" in data and "output" in data:
            req_id = data.get("requestId") or data.get("request_id") or raw.get("id", "")
            flat_records.append({
                "request_id": req_id or hashlib.sha256(msg.encode()).hexdigest()[:16],
                "prompt": _stringify(data.get("input", "")),
                "response": _stringify(data.get("output", "")),
                "timestamp": raw.get("timestamp"),
            })
            continue

        # OTel gen_ai event
        event_name = data.get("eventName") or ""
        if not event_name.startswith("gen_ai."):
            continue
        trace_id = data.get("traceId") or ""
        if not trace_id:
            continue

        body = data.get("body") or {}
        text = _otel_text(body)
        if not text:
            continue

        slot = by_trace.setdefault(trace_id, {"prompt": "", "response": "", "timestamp": raw.get("timestamp")})
        if event_name == "gen_ai.user.message":
            # Capture the FIRST user message in the trace as the original
            # prompt; tool-result messages re-issued as user messages are
            # ignored so the rubric grades the user's actual input.
            if not slot["prompt"]:
                slot["prompt"] = text
        elif event_name in {"gen_ai.assistant.message", "gen_ai.choice"}:
            # Take the LAST assistant/choice text — that's the final output
            # after any tool-use chain.
            slot["response"] = text

    records: list[dict[str, Any]] = list(flat_records)
    for trace_id, slot in by_trace.items():
        prompt = slot["prompt"]
        response = slot["response"]

        # Fast path: complete pair in this batch.
        if prompt and response:
            records.append({
                "request_id": trace_id,
                "prompt": prompt,
                "response": response,
                "timestamp": slot["timestamp"],
            })
            _trace_clear(agent_name, trace_id)
            continue

        # Slow path: stitch via DDB.
        if response and not prompt:
            stashed = _trace_get(agent_name, trace_id)
            if stashed:
                records.append({
                    "request_id": trace_id,
                    "prompt": stashed,
                    "response": response,
                    "timestamp": slot["timestamp"],
                })
                _trace_clear(agent_name, trace_id)
        elif prompt and not response:
            _trace_put(agent_name, trace_id, prompt)
        # else: both empty — ignore.

    return records


def _trace_key(agent_name: str, trace_id: str) -> dict[str, str]:
    return {"trace_id": f"{agent_name}#{trace_id}"}


def _trace_put(agent_name: str, trace_id: str, prompt: str) -> None:
    if not traces_table:
        return
    try:
        traces_table.put_item(Item={
            **_trace_key(agent_name, trace_id),
            "prompt": prompt,
            "ttl": int(time.time()) + TRACE_TTL_SECONDS,
        })
    except ClientError as e:
        log.warning("traces put_item failed: %s", e)


def _trace_get(agent_name: str, trace_id: str) -> Optional[str]:
    if not traces_table:
        return None
    try:
        item = traces_table.get_item(Key=_trace_key(agent_name, trace_id)).get("Item")
    except ClientError as e:
        log.warning("traces get_item failed: %s", e)
        return None
    if not item:
        return None
    return item.get("prompt") or None


def _trace_clear(agent_name: str, trace_id: str) -> None:
    if not traces_table:
        return
    try:
        traces_table.delete_item(Key=_trace_key(agent_name, trace_id))
    except ClientError as e:
        log.warning("traces delete_item failed: %s", e)


def _otel_text(body: Any) -> str:
    """Pull the human-readable text out of an OTel gen_ai event body."""
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""
    content = body.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                elif "type" in item and item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)
    if "text" in body and isinstance(body["text"], str):
        return body["text"]
    return ""


def _stringify(v: Any) -> str:
    return v if isinstance(v, str) else json.dumps(v)


# ─── Judge call ─────────────────────────────────────────────────────────────


def _judge(agent_meta: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    user_prompt = build_user_prompt(
        agent_name=agent_meta["name"],
        agent_description=agent_meta.get("description"),
        user_input=record["prompt"],
        agent_output=record["response"],
    )

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 800,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.0,
    }
    resp = bedrock.invoke_model(
        modelId=JUDGE_MODEL_ID,
        body=json.dumps(body).encode(),
        accept="application/json",
        contentType="application/json",
    )
    raw = json.loads(resp["body"].read())
    text = raw["content"][0]["text"].strip()

    # The model is instructed to return JSON only. Be defensive against an
    # accidental ```json fence so a single misbehaving response doesn't lose
    # the entire verdict.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    return json.loads(text)


# ─── Sinks: CloudWatch metrics + S3 JSONL ───────────────────────────────────


def _emit(agent_name: str, agent_type: str, record: dict[str, Any], verdict: dict[str, Any]) -> None:
    overall = float(verdict.get("overall", 0.0))
    scores = verdict.get("scores", {})

    metric_data = []
    for dim_name in ("correctness", "helpfulness", "safety"):
        dim = scores.get(dim_name, {})
        if "score" in dim:
            metric_data.append(_metric(dim_name.title(), float(dim["score"]), agent_name, agent_type))
    metric_data.append(_metric("Score", overall, agent_name, agent_type))

    try:
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data)
    except ClientError as e:
        log.warning("cloudwatch put_metric_data failed: %s", e)

    log.info(
        json.dumps({
            "agent": agent_name,
            "agent_type": agent_type,
            "request_id": record["request_id"],
            "overall": overall,
            "verdict": verdict.get("verdict"),
            "summary": verdict.get("summary"),
        })
    )

    record_for_s3 = {
        "agent_name": agent_name,
        "agent_type": agent_type,
        "region": REGION,
        "request_id": record["request_id"],
        "prompt": record["prompt"],
        "response": record["response"],
        "verdict": verdict,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    dt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"verdicts/agent_name={agent_name}/dt={dt}/{record['request_id']}.jsonl"
    body = (json.dumps(record_for_s3) + "\n").encode()
    try:
        s3.put_object(Bucket=VERDICTS_BUCKET, Key=key, Body=body, ContentType="application/x-jsonlines")
    except ClientError as e:
        log.warning("s3 put_object failed: %s", e)

    # Also append a row to the BYOI dataset so the daily batch layer can
    # submit it as an Amazon Bedrock Evaluation job (managed service that
    # surfaces results on the Bedrock console's Evaluations page).
    byoi_row = {
        "prompt": record["prompt"],
        "category": agent_type,
        "modelResponses": [{
            "response": record["response"],
            "modelIdentifier": agent_name,
        }],
    }
    dataset_key = f"dataset/agent_name={agent_name}/dt={dt}/{record['request_id']}.jsonl"
    try:
        s3.put_object(
            Bucket=VERDICTS_BUCKET,
            Key=dataset_key,
            Body=(json.dumps(byoi_row) + "\n").encode(),
            ContentType="application/x-jsonlines",
        )
    except ClientError as e:
        log.warning("s3 put_object (dataset) failed: %s", e)


def _metric(name: str, value: float, agent_name: str, agent_type: str) -> dict[str, Any]:
    return {
        "MetricName": name,
        "Value": value,
        "Unit": "None",
        "Dimensions": [
            {"Name": "AgentName", "Value": agent_name},
            {"Name": "AgentType", "Value": agent_type},
            {"Name": "Region", "Value": REGION},
        ],
    }
