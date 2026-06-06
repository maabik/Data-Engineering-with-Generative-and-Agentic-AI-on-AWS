"""CDC ingest Lambda: simulates Change Data Capture from a source system.

Writes staged records to the Landing Zone bucket in Hive-partitioned JSON.
Supports INSERT, UPDATE, DELETE operation types to simulate a CDC stream.

Environment:
  LANDING_BUCKET  — S3 bucket name for landing zone writes
  SOURCE_TABLE    — logical source table name (default: chicago_buildings)
"""

import datetime
import json
import logging
import os
import random

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANDING_BUCKET = os.environ["LANDING_BUCKET"]
SOURCE_TABLE = os.environ.get("SOURCE_TABLE", "chicago_buildings")

s3 = boto3.client("s3")

SAMPLE_RECORDS = [
    {"building_id": "CHI-001", "address": "100 N State St", "year_built": 1985, "floors": 22, "use_type": "Office"},
    {"building_id": "CHI-002", "address": "200 W Madison St", "year_built": 1970, "floors": 40, "use_type": "Office"},
    {"building_id": "CHI-003", "address": "333 W Wacker Dr", "year_built": 1983, "floors": 36, "use_type": "Office"},
    {"building_id": "CHI-004", "address": "77 W Wacker Dr", "year_built": 1992, "floors": 50, "use_type": "Mixed"},
    {"building_id": "CHI-005", "address": "225 W Randolph St", "year_built": 1963, "floors": 19, "use_type": "Office"},
    {"building_id": "CHI-006", "address": "55 E Monroe St", "year_built": 1972, "floors": 21, "use_type": "Mixed"},
    {"building_id": "CHI-007", "address": "150 N Michigan Ave", "year_built": 1974, "floors": 28, "use_type": "Office"},
    {"building_id": "CHI-008", "address": "1 N Dearborn St", "year_built": 2005, "floors": 33, "use_type": "Residential"},
    {"building_id": "CHI-009", "address": "200 N Michigan Ave", "year_built": 1990, "floors": 15, "use_type": "Retail"},
    {"building_id": "CHI-010", "address": "444 N Michigan Ave", "year_built": 1968, "floors": 8, "use_type": "Retail"},
]

CDC_OPERATIONS = ["INSERT", "UPDATE", "DELETE"]


def lambda_handler(event, context):
    now = datetime.datetime.utcnow()

    # Generate a small batch of CDC events
    batch_size = event.get("batch_size", random.randint(3, 8))
    records = []
    for _ in range(batch_size):
        base = random.choice(SAMPLE_RECORDS).copy()
        op = random.choices(CDC_OPERATIONS, weights=[60, 30, 10])[0]
        records.append({
            "_cdc_op": op,
            "_cdc_ts": now.isoformat() + "Z",
            "_cdc_source": SOURCE_TABLE,
            **base,
        })

    key = (
        f"cdc/{SOURCE_TABLE}/"
        f"year={now.year}/month={now.month:02d}/"
        f"day={now.day:02d}/hour={now.hour:02d}/"
        f"batch_{now.strftime('%Y%m%d_%H%M%S')}_{random.randint(1000,9999)}.json"
    )

    payload = "\n".join(json.dumps(r) for r in records)  # newline-delimited JSON
    s3.put_object(
        Bucket=LANDING_BUCKET,
        Key=key,
        Body=payload.encode(),
        ContentType="application/x-ndjson",
    )
    logger.info("CDC batch: %d records (%s) → s3://%s/%s", batch_size, [r["_cdc_op"] for r in records], LANDING_BUCKET, key)

    return {
        "statusCode": 200,
        "records_written": batch_size,
        "s3_key": key,
    }
