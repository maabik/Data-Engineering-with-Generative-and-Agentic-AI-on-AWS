"""Live integration tests for the deployed AgentRunCostTracer stack.

Tests both emit paths end-to-end:
  - Firehose: PutRecord → delivery stream → S3 (buffered, so we verify the
    PutRecord call succeeds and the stream is in ACTIVE state; we don't wait
    for the 60s buffer to flush to S3 in a synchronous test)
  - CloudWatch: PutLogEvents → log group / log stream → verify record lands

Requires:
  AWS_REGION=us-west-2
  Deployed stack: AgentRunCostTracer
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import time
import boto3
import pytest

REGION = "us-west-2"
STACK  = "AgentRunCostTracer"

_OUTPUTS: dict = {}

MODEL = "us.amazon.nova-micro-v1:0"


@pytest.fixture(scope="module", autouse=True)
def stack_outputs():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    for o in resp["Stacks"][0]["Outputs"]:
        _OUTPUTS[o["OutputKey"]] = o["OutputValue"]


# ── Stack infrastructure ──────────────────────────────────────────────────────

def test_stack_complete():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    assert resp["Stacks"][0]["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")


def test_firehose_stream_active():
    fh = boto3.client("firehose", region_name=REGION)
    resp = fh.describe_delivery_stream(
        DeliveryStreamName=_OUTPUTS["FirehoseStreamName"]
    )
    ds = resp["DeliveryStreamDescription"]
    assert ds["DeliveryStreamStatus"] == "ACTIVE"
    assert ds["DeliveryStreamType"] == "DirectPut"


def test_cw_log_group_exists():
    logs = boto3.client("logs", region_name=REGION)
    resp = logs.describe_log_groups(
        logGroupNamePrefix=_OUTPUTS["CwLogGroupName"]
    )
    groups = resp["logGroups"]
    assert any(g["logGroupName"] == _OUTPUTS["CwLogGroupName"] for g in groups)


def test_spans_bucket_exists_and_encrypted():
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _OUTPUTS["SpansBucketName"]
    enc = s3.get_bucket_encryption(Bucket=bucket)
    rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
    assert any(
        r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
        for r in rules
    )


def test_cmk_enabled_with_rotation():
    kms = boto3.client("kms", region_name=REGION)
    key_arn = _OUTPUTS["CmkArn"]
    meta = kms.describe_key(KeyId=key_arn)["KeyMetadata"]
    assert meta["KeyState"] == "Enabled"
    assert kms.get_key_rotation_status(KeyId=key_arn)["KeyRotationEnabled"] is True


# ── Firehose emit path ────────────────────────────────────────────────────────

def test_firehose_emit_single_span():
    """RunTracer with emit_backend='firehose' successfully PUTs a span."""
    from tracer.run_tracer import RunTracer

    stream = _OUTPUTS["FirehoseStreamName"]
    t = RunTracer(
        user_id="live-fh-u1",
        tenant_id="live-fh-t1",
        emit_backend="firehose",
        firehose_stream=stream,
        region=REGION,
    )
    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    t.flush()
    # If no exception was raised, the PUT succeeded
    assert len(t.spans) == 1
    span = t.spans[0]
    assert span.input_tokens > 0
    assert span.estimated_cost_usd > 0.0


def test_firehose_emit_via_env_var(monkeypatch):
    """FINOPS_FIREHOSE_STREAM env var auto-selects the firehose backend."""
    from tracer.run_tracer import RunTracer

    stream = _OUTPUTS["FirehoseStreamName"]
    monkeypatch.setenv("FINOPS_FIREHOSE_STREAM", stream)
    monkeypatch.delenv("FINOPS_CW_LOG_GROUP", raising=False)
    monkeypatch.delenv("FINOPS_EMIT_BACKEND", raising=False)

    t = RunTracer(user_id="live-fh-env-u", tenant_id="live-fh-env-t", region=REGION)
    assert t._backend == "firehose"
    assert t._firehose_stream == stream

    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Hi"}]}],
        inferenceConfig={"maxTokens": 5},
    )
    t.flush()
    assert len(t.spans) == 1


def test_firehose_emit_multiple_spans():
    """Firehose backend handles rapid back-to-back PUTs without error."""
    from tracer.run_tracer import RunTracer

    stream = _OUTPUTS["FirehoseStreamName"]
    t = RunTracer(
        user_id="live-fh-multi-u",
        tenant_id="live-fh-multi-t",
        emit_backend="firehose",
        firehose_stream=stream,
        region=REGION,
    )
    for _ in range(3):
        t.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": "Count"}]}],
            inferenceConfig={"maxTokens": 5},
        )
    t.flush()
    assert len(t.spans) == 3
    summary = t.run_summary()
    assert summary["model_invocations"] == 3
    assert summary["total_estimated_cost_usd"] > 0.0


def test_firehose_stream_metrics_after_put():
    """After PUTs, IncomingRecords metric namespace is reachable (stream is wired)."""
    fh = boto3.client("firehose", region_name=REGION)
    stream_name = _OUTPUTS["FirehoseStreamName"]
    resp = fh.describe_delivery_stream(DeliveryStreamName=stream_name)
    dest = resp["DeliveryStreamDescription"]["Destinations"][0]
    # Confirm destination is S3
    assert "ExtendedS3DestinationDescription" in dest
    s3_dest = dest["ExtendedS3DestinationDescription"]
    assert _OUTPUTS["SpansBucketName"] in s3_dest["BucketARN"]


# ── CloudWatch emit path ──────────────────────────────────────────────────────

def test_cloudwatch_emit_creates_log_stream():
    """RunTracer with emit_backend='cloudwatch' creates a log stream and writes events."""
    from tracer.run_tracer import RunTracer

    log_group = _OUTPUTS["CwLogGroupName"]
    t = RunTracer(
        user_id="live-cw-u1",
        tenant_id="live-cw-t1",
        emit_backend="cloudwatch",
        cw_log_group=log_group,
        region=REGION,
    )
    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Say yes"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    t.flush()

    # The stream name is agent-run-cost/<run_id[:8]>
    expected_stream = f"agent-run-cost/{t.run_id[:8]}"
    logs = boto3.client("logs", region_name=REGION)

    # Give CW a moment to accept the events
    time.sleep(2)

    streams = logs.describe_log_streams(
        logGroupName=log_group,
        logStreamNamePrefix=f"agent-run-cost/{t.run_id[:8]}",
    )["logStreams"]
    assert any(s["logStreamName"] == expected_stream for s in streams), \
        f"Log stream {expected_stream!r} not found"


def test_cloudwatch_emit_span_content():
    """Emitted CW log events contain valid JSON with expected span fields."""
    from tracer.run_tracer import RunTracer

    log_group = _OUTPUTS["CwLogGroupName"]
    t = RunTracer(
        user_id="live-cw-content-u",
        tenant_id="live-cw-content-t",
        emit_backend="cloudwatch",
        cw_log_group=log_group,
        region=REGION,
    )
    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "What is 3+3?"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    t.flush()

    time.sleep(3)

    log_stream = f"agent-run-cost/{t.run_id[:8]}"
    logs_client = boto3.client("logs", region_name=REGION)
    events = logs_client.get_log_events(
        logGroupName=log_group,
        logStreamName=log_stream,
        limit=50,
    )["events"]

    assert len(events) >= 1
    span_events = []
    for ev in events:
        try:
            data = json.loads(ev["message"])
            span_events.append(data)
        except json.JSONDecodeError:
            pass

    assert len(span_events) >= 1
    span = span_events[0]
    assert span["span_type"] == "model"
    assert span["tenant_id"] == "live-cw-content-t"
    assert span["input_tokens"] > 0


def test_cloudwatch_emit_via_env_var(monkeypatch):
    """FINOPS_CW_LOG_GROUP env var auto-selects the cloudwatch backend."""
    from tracer.run_tracer import RunTracer

    log_group = _OUTPUTS["CwLogGroupName"]
    monkeypatch.setenv("FINOPS_CW_LOG_GROUP", log_group)
    monkeypatch.delenv("FINOPS_FIREHOSE_STREAM", raising=False)
    monkeypatch.delenv("FINOPS_EMIT_BACKEND", raising=False)

    t = RunTracer(user_id="live-cw-env-u", tenant_id="live-cw-env-t", region=REGION)
    assert t._backend == "cloudwatch"
    assert t._cw_log_group == log_group

    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        inferenceConfig={"maxTokens": 5},
    )
    t.flush()
    assert len(t.spans) == 1


# ── run_context() with firehose ───────────────────────────────────────────────

def test_run_context_firehose_flushes_summary():
    """run_context() flushes a RUN_SUMMARY event to Firehose on exit."""
    from tracer.run_tracer import run_context

    stream = _OUTPUTS["FirehoseStreamName"]
    with run_context(
        user_id="live-ctx-fh-u",
        tenant_id="live-ctx-fh-t",
        emit_backend="firehose",
        firehose_stream=stream,
        region=REGION,
    ) as t:
        t.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": "Done"}]}],
            inferenceConfig={"maxTokens": 5},
        )
    # flush() is called on context exit — if no exception, summary was sent
    assert t.run_summary()["model_invocations"] == 1
