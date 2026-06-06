"""Idempotently create (or update) a DataBrew profile job.

Environment variables:
  JOB_NAME              DataBrew profile job name
  DATASET_NAME          DataBrew dataset to profile
  ROLE_ARN              IAM role ARN for the profile job
  OUTPUT_BUCKET         S3 bucket for profile output
  OUTPUT_KEY_PREFIX     S3 key prefix for profile output
  KMS_KEY_ARN           Optional CMK ARN; if set, output is encrypted with SSE-KMS
  PROFILE_FULL_DATASET  "true" to profile the full dataset (default samples 20k rows)
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DATABREW = boto3.client("databrew")

COLUMN_STATISTICS = [
    "DISTINCT_VALUES_COUNT",
    "UNIQUE_VALUES_COUNT",
    "VALUE_DISTRIBUTION",
    "PERCENTILES",
    "RANGE",
    "SKEWNESS",
    "STANDARD_DEVIATION",
    "VARIANCE",
    "MEAN",
    "MEDIAN",
    "MODE",
    "MIN",
    "MAX",
    "MISSING_VALUES_COUNT",
]


def _build_params():
    job_name = os.environ["JOB_NAME"]
    dataset_name = os.environ["DATASET_NAME"]
    role_arn = os.environ["ROLE_ARN"]
    output_bucket = os.environ["OUTPUT_BUCKET"]
    output_prefix = os.environ["OUTPUT_KEY_PREFIX"]
    kms_key_arn = os.environ.get("KMS_KEY_ARN")
    full_dataset = os.environ.get("PROFILE_FULL_DATASET", "false").lower() == "true"

    params = {
        "Name": job_name,
        "DatasetName": dataset_name,
        "RoleArn": role_arn,
        "OutputLocation": {"Bucket": output_bucket, "Key": output_prefix},
        "Configuration": {
            "ColumnStatisticsConfigurations": [
                {"Statistics": {"IncludedStatistics": COLUMN_STATISTICS}}
            ],
            "DatasetStatisticsConfiguration": {"IncludedStatistics": ["NONE"]},
        },
    }

    if full_dataset:
        params["JobSample"] = {"Mode": "FULL_DATASET"}

    if kms_key_arn:
        params["EncryptionMode"] = "SSE-KMS"
        params["EncryptionKeyArn"] = kms_key_arn

    return params


def lambda_handler(event, context):
    params = _build_params()

    try:
        response = DATABREW.create_profile_job(**params)
        action = "created"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConflictException":
            logger.exception("Failed to create profile job")
            raise
        update_params = {k: v for k, v in params.items() if k != "DatasetName"}
        response = DATABREW.update_profile_job(**update_params)
        action = "updated"

    logger.info("Profile job %s %s", params["Name"], action)

    return {
        "statusCode": 200,
        "body": json.dumps({"job": params["Name"], "action": action}),
        "jobArn": response.get("Name") or response.get("JobArn"),
    }
