from airflow.decorators import dag, task
from airflow.providers.amazon.aws.operators.glue import AwsGlueJobOperator
from datetime import datetime


@dag(
    dag_id='simple_ingest',
    description='Simple AWS Glue ingest job',
    schedule_interval='0 0 * * *',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['ingest', 'glue'],
)
def simple_ingest():
    run_glue_job = AwsGlueJobOperator(
        task_id='run_raw_ingest',
        job_name='raw-ingest',
        aws_conn_id='aws_default',
    )

    run_glue_job


simple_ingest()
