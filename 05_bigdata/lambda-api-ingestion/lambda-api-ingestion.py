import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

HTTP = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5.0, read=25.0), retries=False)
SECRETS = boto3.client("secretsmanager")
S3 = boto3.client("s3")

KMS_KEY_ID = os.environ.get("S3_KMS_KEY_ID")


def _process_record(record):
    body = json.loads(record["body"])
    api_endpoint = body["api_endpoint"]
    secret_name = body["secret_name"]
    api_query = body["api_query"]
    dataset = body["dataset"]
    s3_bucket = body["s3_bucket"]
    s3_prefix = body["s3_prefix"].rstrip("/") + "/"

    secret = SECRETS.get_secret_value(SecretId=secret_name)
    api_key = json.loads(secret["SecretString"]).get("api_key")
    if not api_key:
        raise ValueError(f"API key not found in secret {secret_name}")

    response = HTTP.request(
        "GET",
        api_endpoint + api_query,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.status >= 400:
        raise RuntimeError(
            f"API call failed status={response.status} body={response.data[:200]!r}"
        )

    now = datetime.now(timezone.utc)
    s3_key = (
        f"{s3_prefix}dataset={dataset}/ingest_date={now:%Y-%m-%d}/hour={now:%H}/"
        f"{dataset}_{now:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex}.json"
    )

    put_args = {
        "Bucket": s3_bucket,
        "Key": s3_key,
        "Body": response.data,
        "ContentType": "application/json",
    }
    if KMS_KEY_ID:
        put_args["ServerSideEncryption"] = "aws:kms"
        put_args["SSEKMSKeyId"] = KMS_KEY_ID
    else:
        put_args["ServerSideEncryption"] = "AES256"

    S3.put_object(**put_args)
    logger.info("Wrote s3://%s/%s", s3_bucket, s3_key)


def lambda_handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception:
            logger.exception("Failed to process SQS message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
