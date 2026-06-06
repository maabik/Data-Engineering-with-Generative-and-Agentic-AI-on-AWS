"""Lambda handler for the daily pipeline run (EventBridge → Lambda)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

from .runner import run

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    # Default: process yesterday's data
    target_date_str = event.get("date") or (date.today() - timedelta(days=1)).isoformat()
    target_date = date.fromisoformat(target_date_str)

    result = run(
        start_date=target_date,
        end_date=target_date,
        bedrock_log_bucket=os.environ["BEDROCK_LOG_BUCKET"],
        bedrock_log_prefix=os.environ.get("BEDROCK_LOG_BUCKET_PREFIX", ""),
        cur_bucket=os.environ.get("CUR_BUCKET", ""),
        cur_prefix=os.environ.get("CUR_PREFIX", ""),
        iceberg_database=os.environ.get("ICEBERG_DATABASE", "ai_finops"),
        iceberg_table=os.environ.get("ICEBERG_TABLE", "invocations"),
        athena_output=os.environ["ATHENA_OUTPUT"],
        staging_bucket=os.environ["STAGING_BUCKET"],
        region=os.environ.get("REGION"),
        account_id=os.environ.get("ACCOUNT_ID", ""),
    )
    return {"statusCode": 200, "records_processed": len(result)}
