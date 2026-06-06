"""Live integration tests for the deployed ModelCascadeRouterEval stack.

Covers:
  1. Stack infra: S3 bucket, KMS CMK, IAM roles
  2. EvalHarness.run_local()  — LLM-as-judge against Nova Micro
  3. EvalHarness.run_managed() — submit real Bedrock Model Evaluation job
  4. EvalHarness.poll_managed() — wait for job completion and verify results

The managed job (test 3+4) typically takes 10–20 minutes. It is marked
with @pytest.mark.slow and skipped unless --run-slow is passed.

Run all tests:
    pytest tests/test_live_eval.py -v --run-slow
Run fast tests only:
    pytest tests/test_live_eval.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import time
import boto3
import pytest

REGION = "us-west-2"
STACK  = "ModelCascadeRouterEval"

# Cheap inference profiles for testing
MODEL_NANO   = "us.amazon.nova-micro-v1:0"
MODEL_JUDGE  = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # cheap judge

_OUTPUTS: dict = {}


@pytest.fixture(scope="module", autouse=True)
def stack_outputs():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    for o in resp["Stacks"][0]["Outputs"]:
        _OUTPUTS[o["OutputKey"]] = o["OutputValue"]


# ── Stack infra ───────────────────────────────────────────────────────────────

def test_stack_complete():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    assert resp["Stacks"][0]["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")


def test_eval_bucket_exists_and_encrypted():
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _OUTPUTS["EvalBucketName"]
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


def test_eval_role_exists_trusted_by_bedrock():
    iam = boto3.client("iam", region_name=REGION)
    role_arn = _OUTPUTS["EvalRoleArn"]
    role_name = role_arn.split("/")[-1]
    resp = iam.get_role(RoleName=role_name)
    trust = json.dumps(resp["Role"]["AssumeRolePolicyDocument"])
    assert "bedrock.amazonaws.com" in trust


def test_caller_role_exists():
    iam = boto3.client("iam", region_name=REGION)
    role_arn = _OUTPUTS["CallerRoleArn"]
    role_name = role_arn.split("/")[-1]
    resp = iam.get_role(RoleName=role_name)
    assert resp["Role"]["RoleName"] == role_name


# ── EvalHarness.run_local() ───────────────────────────────────────────────────

def _make_suite():
    from eval.harness import EvalSuite
    return EvalSuite.from_list("live-test-suite", [
        {
            "case_id": "q1",
            "messages": [{"role": "user", "content": [{"text": "What is the capital of France?"}]}],
            "expected_answer": "Paris",
        },
        {
            "case_id": "q2",
            "messages": [{"role": "user", "content": [{"text": "What is 7 times 6?"}]}],
            "expected_answer": "42",
        },
        {
            "case_id": "q3",
            "messages": [{"role": "user", "content": [{"text": "What does CPU stand for?"}]}],
            "expected_answer": "Central Processing Unit",
        },
    ])


def test_run_local_returns_report():
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=2.5, max_tokens=50)
    assert report.total == 3
    assert report.model_id == MODEL_NANO
    assert report.suite_name == "live-test-suite"


def test_run_local_all_cases_scored():
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=2.5, max_tokens=50)
    for result in report.results:
        assert result.error is None, f"Case {result.case_id} errored: {result.error}"
        assert 1.0 <= result.score <= 5.0
        assert result.input_tokens > 0
        assert result.output_tokens > 0


def test_run_local_cost_is_positive():
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=2.5, max_tokens=50)
    assert report.total_cost_usd > 0.0


def test_run_local_nova_micro_passes_simple_qa():
    """Nova Micro should pass at least 1/3 cases on trivial factual QA with min_score=3.5."""
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=3.5, max_tokens=100)
    assert report.pass_rate >= 0.33, (
        f"Expected ≥33% pass rate, got {report.pass_rate:.0%}\n{report.summary()}"
    )


def test_eval_report_summary_format():
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=3.0, max_tokens=50)
    summary = report.summary()
    assert "Model:" in summary
    assert "Passed" in summary
    assert "avg score" in summary
    assert "cost" in summary


def test_eval_report_to_dict_serialisable():
    from eval.harness import EvalHarness
    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt, judge_model_id=MODEL_JUDGE)
    suite = _make_suite()
    report = harness.run_local(suite, model_id=MODEL_NANO, min_score=3.0, max_tokens=50)
    d = report.to_dict()
    # Must be JSON-serialisable
    blob = json.dumps(d)
    parsed = json.loads(blob)
    assert parsed["total"] == 3
    assert len(parsed["results"]) == 3


# ── EvalHarness.run_managed() + poll_managed() ────────────────────────────────

@pytest.mark.slow
def test_run_managed_submits_job():
    """Upload dataset to S3, submit managed eval job, verify ARN returned."""
    from eval.harness import EvalHarness, EvalSuite
    import re

    suite = EvalSuite.from_jsonl(
        os.path.join(os.path.dirname(__file__), "..", "eval", "suites", "qa_simple.jsonl")
    )
    bucket = _OUTPUTS["EvalBucketName"]
    eval_role_arn = _OUTPUTS["EvalRoleArn"]
    output_s3_uri = f"s3://{bucket}/results/"

    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt)

    job_arn = harness.run_managed(
        suite=suite,
        model_id=MODEL_NANO,
        output_s3_uri=output_s3_uri,
        role_arn=eval_role_arn,
        eval_name="live-test-managed-eval",
    )
    assert job_arn.startswith("arn:aws:bedrock:")
    # Store for poll test
    _OUTPUTS["_managed_job_arn"] = job_arn


@pytest.mark.slow
def test_dataset_uploaded_to_s3():
    """After run_managed(), the dataset JSONL should be present in S3."""
    bucket = _OUTPUTS["EvalBucketName"]
    s3 = boto3.client("s3", region_name=REGION)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="results/datasets/")
    keys = [o["Key"] for o in resp.get("Contents", [])]
    assert any("qa_simple.jsonl" in k for k in keys), \
        f"Dataset not found in S3. Found: {keys}"


@pytest.mark.slow
def test_poll_managed_completes(request):
    """Poll the submitted job until Completed (up to 30 min)."""
    from eval.harness import EvalHarness
    job_arn = _OUTPUTS.get("_managed_job_arn")
    if not job_arn:
        pytest.skip("No job ARN from test_run_managed_submits_job")

    brt = boto3.client("bedrock-runtime", region_name=REGION)
    harness = EvalHarness(bedrock_client=brt)

    result = harness.poll_managed(
        job_arn=job_arn,
        timeout_s=1800,      # 30 min ceiling
        poll_interval_s=30,
    )
    assert result.get("status") == "Completed", f"Job ended with status: {result.get('status')}"
    # Output location must be set
    assert "outputDataConfig" in result


@pytest.mark.slow
def test_managed_results_written_to_s3():
    """After completion, Bedrock should have written result files to S3."""
    bucket = _OUTPUTS["EvalBucketName"]
    s3 = boto3.client("s3", region_name=REGION)
    resp = s3.list_objects_v2(Bucket=bucket, Prefix="results/")
    keys = [o["Key"] for o in resp.get("Contents", [])]
    result_files = [k for k in keys if "datasets" not in k]
    assert len(result_files) > 0, \
        f"No result files found under s3://{bucket}/results/. Keys: {keys}"
