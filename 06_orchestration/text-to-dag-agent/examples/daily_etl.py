from datetime import datetime
from airflow import DAG
from airflow.providers.amazon.aws.operators.glue import AwsGlueJobOperator
from airflow.models import Variable

default_args = {
    "owner": "airflow",
    "retries": 1,
}

with DAG(
    dag_id="daily_etl",
    default_args=default_args,
    description="Daily ETL pipeline with Glue jobs",
    schedule="0 2 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["etl", "glue"],
) as dag:

    raw_ingest = AwsGlueJobOperator(
        task_id="raw_ingest",
        job_name="raw_ingest",
        script_location=None,
        s3_bucket=None,
        iam_role_name=None,
        create_job_kwargs={},
        wait_for_completion=True,
    )

    transform = AwsGlueJobOperator(
        task_id="transform",
        job_name="transform",
        script_location=None,
        s3_bucket=None,
        iam_role_name=None,
        create_job_kwargs={},
        wait_for_completion=True,
    )

    publish = AwsGlueJobOperator(
        task_id="publish",
        job_name="publish",
        script_location=None,
        s3_bucket=None,
        iam_role_name=None,
        create_job_kwargs={},
        wait_for_completion=True,
    )

    raw_ingest >> transform >> publish
