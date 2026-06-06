"""Live integration tests for the deployed AIFinOpsLake stack.

Requires:
  AWS_REGION=us-west-2
  Deployed stack: AIFinOpsLake
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import time
import boto3
import pytest

REGION = "us-west-2"
STACK  = "AIFinOpsLake"

# Stack outputs (populated via fixture)
_OUTPUTS: dict = {}

@pytest.fixture(scope="module", autouse=True)
def stack_outputs():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    for o in resp["Stacks"][0]["Outputs"]:
        _OUTPUTS[o["OutputKey"]] = o["OutputValue"]


# ── Stack state ───────────────────────────────────────────────────────────────

def test_stack_is_complete():
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=STACK)
    assert resp["Stacks"][0]["StackStatus"] in (
        "CREATE_COMPLETE", "UPDATE_COMPLETE"
    )


# ── S3 buckets ────────────────────────────────────────────────────────────────

def test_iceberg_bucket_exists_and_encrypted():
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _OUTPUTS["IcebergBucketName"]
    enc = s3.get_bucket_encryption(Bucket=bucket)
    rules = enc["ServerSideEncryptionConfiguration"]["Rules"]
    assert any(
        r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
        for r in rules
    )


def test_staging_bucket_exists():
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _OUTPUTS["StagingBucketName"]
    resp = s3.head_bucket(Bucket=bucket)
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_athena_results_bucket_exists():
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _OUTPUTS["AthenaResultsBucketName"]
    resp = s3.head_bucket(Bucket=bucket)
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


# ── KMS CMK ───────────────────────────────────────────────────────────────────

def test_lake_cmk_enabled_with_rotation():
    kms = boto3.client("kms", region_name=REGION)
    key_arn = _OUTPUTS["LakeCmkArn"]
    meta = kms.describe_key(KeyId=key_arn)["KeyMetadata"]
    assert meta["KeyState"] == "Enabled"
    rotation = kms.get_key_rotation_status(KeyId=key_arn)
    assert rotation["KeyRotationEnabled"] is True


# ── Glue database ─────────────────────────────────────────────────────────────

def test_glue_database_exists():
    glue = boto3.client("glue", region_name=REGION)
    resp = glue.get_database(Name="ai_finops")
    assert resp["Database"]["Name"] == "ai_finops"


def test_glue_invocations_table_is_iceberg():
    glue = boto3.client("glue", region_name=REGION)
    resp = glue.get_table(DatabaseName="ai_finops", Name="invocations")
    params = resp["Table"].get("Parameters", {})
    assert params.get("table_type", "").upper() == "ICEBERG"


def test_glue_invocations_table_has_expected_columns():
    glue = boto3.client("glue", region_name=REGION)
    resp = glue.get_table(DatabaseName="ai_finops", Name="invocations")
    col_names = {c["Name"] for c in resp["Table"]["StorageDescriptor"]["Columns"]}
    required = {
        "event_time", "model_id", "tenant_id", "input_tokens",
        "output_tokens", "estimated_cost_usd", "execution_status",
    }
    assert required <= col_names


# ── Athena workgroup ──────────────────────────────────────────────────────────

def test_athena_workgroup_exists_and_configured():
    athena = boto3.client("athena", region_name=REGION)
    resp = athena.get_work_group(WorkGroup="ai-finops")
    wg = resp["WorkGroup"]
    assert wg["State"] == "ENABLED"
    cfg = wg["Configuration"]
    assert cfg.get("EnforceWorkGroupConfiguration") is True
    # 10 GB per-query scan limit
    assert cfg.get("BytesScannedCutoffPerQuery") == 10 * 1024 ** 3


# ── Lambda daily runner ───────────────────────────────────────────────────────

def test_daily_runner_lambda_exists():
    lc = boto3.client("lambda", region_name=REGION)
    fn = _OUTPUTS["DailyRunnerFunctionName"]
    resp = lc.get_function(FunctionName=fn)
    cfg = resp["Configuration"]
    assert cfg["State"] == "Active"
    assert cfg["Runtime"] == "python3.12"
    assert cfg["Timeout"] == 900  # 15 minutes


def test_daily_runner_lambda_has_required_env_vars():
    lc = boto3.client("lambda", region_name=REGION)
    fn = _OUTPUTS["DailyRunnerFunctionName"]
    resp = lc.get_function_configuration(FunctionName=fn)
    env = resp["Environment"]["Variables"]
    for key in ("ICEBERG_DATABASE", "ICEBERG_TABLE", "ATHENA_OUTPUT",
                "STAGING_BUCKET", "BEDROCK_LOG_BUCKET"):
        assert key in env, f"Missing env var: {key}"


# ── IAM pipeline role ─────────────────────────────────────────────────────────

def test_pipeline_role_exists():
    iam = boto3.client("iam", region_name=REGION)
    role_arn = _OUTPUTS["PipelineRoleArn"]
    role_name = role_arn.split("/")[-1]
    resp = iam.get_role(RoleName=role_name)
    assert resp["Role"]["RoleName"] == role_name


# ── Glue backfill job ─────────────────────────────────────────────────────────

def test_glue_backfill_job_exists():
    glue = boto3.client("glue", region_name=REGION)
    resp = glue.get_job(JobName="ai-finops-backfill")
    job = resp["Job"]
    assert job["Command"]["Name"] == "pythonshell"
    assert job["MaxCapacity"] == 1.0
