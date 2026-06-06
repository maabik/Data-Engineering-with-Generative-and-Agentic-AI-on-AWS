"""Integration tests against a deployed sandbox.

Prerequisites: `terraform apply` has succeeded in
reference-pipeline/terraform/envs/sandbox with a workspace named after the
test runner. The fixtures below shell out to `terraform output -json` to
discover resource names.

Slow on purpose — Glue cold starts dominate. Run on PR open / push, not on
every commit. See ../docs/testing-data-pipelines.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import boto3
import pytest

TF_DIR = Path(__file__).resolve().parents[2] / "terraform" / "envs" / "sandbox"
GLUE_TIMEOUT = int(os.environ.get("GLUE_TIMEOUT", "600"))


@pytest.fixture(scope="session")
def tf_outputs() -> dict[str, str]:
    raw = subprocess.check_output(
        ["terraform", "output", "-json"], cwd=TF_DIR, text=True
    )
    return {k: v["value"] for k, v in json.loads(raw).items()}


def _wait_for_execution(arn: str) -> str:
    sfn = boto3.client("stepfunctions")
    deadline = time.time() + GLUE_TIMEOUT
    while time.time() < deadline:
        status = sfn.describe_execution(executionArn=arn)["status"]
        if status not in {"RUNNING"}:
            return status
        time.sleep(15)
    raise TimeoutError(f"Execution {arn} did not complete within {GLUE_TIMEOUT}s")


def test_state_machine_runs_to_success(tf_outputs: dict[str, str]) -> None:
    sfn = boto3.client("stepfunctions")
    started = sfn.start_execution(stateMachineArn=tf_outputs["step_functions_arn"])
    final = _wait_for_execution(started["executionArn"])
    assert final == "SUCCEEDED", f"State machine ended in {final}"


def test_curated_table_is_queryable(tf_outputs: dict[str, str]) -> None:
    athena = boto3.client("athena")
    started = athena.start_query_execution(
        QueryString=f"SELECT count(*) FROM {tf_outputs['curated_database']}.region_revenue",
        ResultConfiguration={"OutputLocation": f"s3://{tf_outputs['curated_bucket']}/_athena/"},
    )["QueryExecutionId"]

    deadline = time.time() + 120
    while time.time() < deadline:
        state = athena.get_query_execution(QueryExecutionId=started)["QueryExecution"]["Status"]["State"]
        if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(3)
    assert state == "SUCCEEDED", f"Athena query ended in {state}"

    rows = athena.get_query_results(QueryExecutionId=started)["ResultSet"]["Rows"]
    count = int(rows[1]["Data"][0]["VarCharValue"])
    assert count > 0
