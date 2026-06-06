"""Post-deploy smoke tests.

Run after a successful Terraform apply, against the target environment.
The terraform_outputs fixture is shared with the integration suite —
same shape, different target environment.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import boto3
import pytest


@pytest.fixture(scope="session")
def tf_outputs() -> dict[str, str]:
    tf_dir = os.environ.get("TF_DIR", "reference-pipeline/terraform/envs/sandbox")
    raw = subprocess.check_output(["terraform", "output", "-json"], cwd=Path(tf_dir).resolve(), text=True)
    return {k: v["value"] for k, v in json.loads(raw).items()}


def test_state_machine_has_recent_success(tf_outputs: dict[str, str]) -> None:
    sfn = boto3.client("stepfunctions")
    history = sfn.list_executions(
        stateMachineArn=tf_outputs["step_functions_arn"],
        statusFilter="SUCCEEDED",
        maxResults=1,
    )
    assert history["executions"], "state machine has never succeeded"


def test_curated_table_returns_rows(tf_outputs: dict[str, str]) -> None:
    athena = boto3.client("athena")
    qid = athena.start_query_execution(
        QueryString=f"SELECT count(*) FROM {tf_outputs['curated_database']}.region_revenue",
        ResultConfiguration={"OutputLocation": f"s3://{tf_outputs['curated_bucket']}/_athena_smoke/"},
    )["QueryExecutionId"]
    deadline = time.time() + 60
    while time.time() < deadline:
        s = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if s in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(2)
    assert s == "SUCCEEDED"
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    assert int(rows[1]["Data"][0]["VarCharValue"]) > 0
