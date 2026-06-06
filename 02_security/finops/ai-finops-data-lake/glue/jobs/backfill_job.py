"""Glue Python Shell job: backfill the AI FinOps Iceberg table.

For historical loads too large for the Athena INSERT path. Run via:
    aws glue start-job-run --job-name ai-finops-backfill \\
      --arguments '--start_date=2025-01-01,--end_date=2025-12-31,...'

Job parameters (passed as --key=value Glue arguments):
  --start_date            YYYY-MM-DD
  --end_date              YYYY-MM-DD
  --bedrock_log_bucket    S3 bucket for bedrock-audit-trail NDJSON
  --bedrock_log_prefix    S3 prefix (optional)
  --cur_bucket            S3 bucket for CUR 2.0 Parquet exports
  --cur_prefix            S3 prefix under cur_bucket
  --iceberg_database      Glue database (default: ai_finops)
  --iceberg_table         Glue table (default: invocations)
  --athena_output         s3://... Athena query result location
  --staging_bucket        S3 bucket for temp NDJSON staging
  --region                AWS region
  --account_id            AWS account ID
"""

from __future__ import annotations

import sys
from datetime import date

# Glue provides getResolvedOptions
try:
    from awsglue.utils import getResolvedOptions
    args_raw = getResolvedOptions(sys.argv, [
        "start_date", "end_date",
        "bedrock_log_bucket",
        "cur_bucket",
        "iceberg_database",
        "iceberg_table",
        "athena_output",
        "staging_bucket",
    ])
    is_glue = True
except ImportError:
    # Local / test invocation
    args_raw = {}
    is_glue = False


def _arg(key: str, default: str = "") -> str:
    return args_raw.get(key, default)


# Add the job directory to sys.path so pipeline imports work
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pipeline.runner import run

run(
    start_date=date.fromisoformat(_arg("start_date", date.today().isoformat())),
    end_date=date.fromisoformat(_arg("end_date", date.today().isoformat())),
    bedrock_log_bucket=_arg("bedrock_log_bucket"),
    bedrock_log_prefix=_arg("bedrock_log_prefix", ""),
    cur_bucket=_arg("cur_bucket", ""),
    cur_prefix=_arg("cur_prefix", ""),
    iceberg_database=_arg("iceberg_database", "ai_finops"),
    iceberg_table=_arg("iceberg_table", "invocations"),
    athena_output=_arg("athena_output"),
    staging_bucket=_arg("staging_bucket"),
    region=_arg("region") or None,
    account_id=_arg("account_id", ""),
)
