"""Airflow DAG mirroring the Step Functions glue-workflow.json example.

Companion to ../examples/glue-workflow.json (Chapter 6, p. 270).

Graph: glue-job-extract1 || glue-job-extract2 -> glue-job-transform
       -> glue-job-validate -> glue-job-publish

On any task failure, an on_failure_callback publishes a structured message
to the SNS topic "glue-workflow-error".
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import boto3
from airflow import DAG
from airflow.models import Variable
from airflow.providers.amazon.aws.operators.glue import GlueJobOperator


SNS_TOPIC_ARN_VAR = "glue_workflow_error_topic_arn"


def notify_sns_on_failure(context: dict) -> None:
    """Publish a failure notification to the glue-workflow-error SNS topic.

    The topic ARN is read from the Airflow Variable
    `glue_workflow_error_topic_arn` so the DAG file stays portable across
    accounts. Set it once in the Airflow UI / `airflow variables set`.
    """
    topic_arn = Variable.get(SNS_TOPIC_ARN_VAR, default_var=None)
    if not topic_arn:
        return

    ti = context.get("task_instance")
    payload = {
        "dag_id": context.get("dag").dag_id if context.get("dag") else None,
        "task_id": ti.task_id if ti else None,
        "execution_date": str(context.get("execution_date")),
        "run_id": context.get("run_id"),
        "log_url": ti.log_url if ti else None,
        "exception": str(context.get("exception")),
    }
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject=f"Glue workflow failure: {payload['task_id']}",
        Message=json.dumps(payload, default=str, indent=2),
    )


default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "on_failure_callback": notify_sns_on_failure,
}


with DAG(
    dag_id="glue_workflow",
    default_args=default_args,
    description=(
        "Glue ETL pipeline: extract1 || extract2 -> transform -> validate "
        "-> publish, with SNS error notification on failure."
    ),
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["etl", "glue", "chapter-6"],
) as dag:

    extract1 = GlueJobOperator(
        task_id="glue_job_extract1",
        job_name="glue-job-extract1",
        wait_for_completion=True,
    )

    extract2 = GlueJobOperator(
        task_id="glue_job_extract2",
        job_name="glue-job-extract2",
        wait_for_completion=True,
    )

    transform = GlueJobOperator(
        task_id="glue_job_transform",
        job_name="glue-job-transform",
        wait_for_completion=True,
    )

    validate = GlueJobOperator(
        task_id="glue_job_validate",
        job_name="glue-job-validate",
        wait_for_completion=True,
    )

    publish = GlueJobOperator(
        task_id="glue_job_publish",
        job_name="glue-job-publish",
        wait_for_completion=True,
    )

    [extract1, extract2] >> transform >> validate >> publish
