"""Pipeline runner: orchestrates all five sources → join → write for a date range.

CLI:
    python -m pipeline.runner \\
        --start-date 2026-01-01 \\
        --end-date   2026-01-31 \\
        --bedrock-log-bucket my-audit-trail-bucket \\
        --cur-bucket         my-cur-bucket \\
        --cur-prefix         cur/bedrock-finops/ \\
        --iceberg-database   ai_finops \\
        --iceberg-table      invocations \\
        --athena-output      s3://my-athena-results/finops/ \\
        --staging-bucket     my-staging-bucket \\
        [--region us-east-1] \\
        [--account-id 123456789012] \\
        [--sample-rate 100] \\
        [--dry-run]

The runner can also be imported and called programmatically (e.g. from a
Glue Python Shell job or a Lambda triggered by EventBridge Scheduler).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

import boto3

from .join import join_all
from .sources import bedrock_logs, cur, inference_profiles, agentcore, adjacent_services
from .writer import write_batch

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def run(
    start_date: date,
    end_date: date,
    *,
    bedrock_log_bucket: str,
    bedrock_log_prefix: str = "",
    cur_bucket: str = "",
    cur_prefix: str = "",
    iceberg_database: str = "ai_finops",
    iceberg_table: str = "invocations",
    athena_output: str,
    staging_bucket: str,
    region: str | None = None,
    account_id: str = "",
    pricing_overrides: dict | None = None,
    dry_run: bool = False,
):
    session = boto3.Session(region_name=region) if region else boto3.Session()
    s3 = session.client("s3")
    cw = session.client("logs")
    bedrock_client = session.client("bedrock")
    athena = session.client("athena")

    logger.info("Extracting Bedrock invocation logs from s3://%s/%s",
                bedrock_log_bucket, bedrock_log_prefix)
    invocation_records = list(bedrock_logs.extract(
        bucket=bedrock_log_bucket,
        prefix=bedrock_log_prefix,
        pricing_overrides=pricing_overrides,
        s3_client=s3,
    ))
    logger.info("  → %d invocation records", len(invocation_records))

    logger.info("Extracting AgentCore Runtime logs (%s → %s)", start_date, end_date)
    agent_records = list(agentcore.extract(
        start_date=start_date,
        end_date=end_date,
        cw_client=cw,
        account_id=account_id,
        region=region or "",
    ))
    logger.info("  → %d AgentCore records", len(agent_records))

    logger.info("Fetching inference profile metadata")
    profile_map = inference_profiles.fetch_profile_map(bedrock_client=bedrock_client)
    logger.info("  → %d profiles", len(profile_map))

    cur_records: list[dict] = []
    if cur_bucket:
        logger.info("Extracting CUR 2.0 from s3://%s/%s", cur_bucket, cur_prefix)
        cur_records = list(cur.extract(
            bucket=cur_bucket,
            prefix=cur_prefix,
            s3_client=s3,
        ))
        logger.info("  → %d CUR records", len(cur_records))
    else:
        logger.info("No CUR bucket configured — falling back to Cost Explorer")
        ce = session.client("ce")
        cur_records = list(adjacent_services.extract_from_cost_explorer(
            start_date=start_date,
            end_date=end_date,
            ce_client=ce,
            account_id=account_id,
            region=region or "",
        ))
        logger.info("  → %d Cost Explorer records", len(cur_records))

    # Enrich invocations with inference profile metadata
    all_invocations = invocation_records + agent_records
    all_invocations = [
        inference_profiles.enrich_record(r, profile_map)
        for r in all_invocations
    ]

    # Join invocations with CUR costs
    logger.info("Joining %d invocation records with %d CUR records",
                len(all_invocations), len(cur_records))
    joined = join_all(all_invocations, cur_records)

    # Adjacent service records (KB, OpenSearch, S3 Vectors) — pass-through
    adjacent = list(adjacent_services.filter_from_cur_records(cur_records))
    logger.info("  → %d adjacent service records", len(adjacent))

    all_records = joined + adjacent

    if dry_run:
        logger.info("[dry-run] Would write %d records to %s.%s",
                    len(all_records), iceberg_database, iceberg_table)
        for r in all_records[:3]:
            logger.info("  sample: %s", r)
        return all_records

    logger.info("Writing %d records to %s.%s", len(all_records),
                iceberg_database, iceberg_table)
    written = write_batch(
        all_records,
        database=iceberg_database,
        table=iceberg_table,
        athena_output_location=athena_output,
        staging_bucket=staging_bucket,
        athena_client=athena,
        s3_client=s3,
    )
    logger.info("Done — wrote %d records", written)
    return all_records


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="AI FinOps data lake pipeline runner")
    p.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--end-date", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--bedrock-log-bucket", required=True)
    p.add_argument("--bedrock-log-prefix", default="")
    p.add_argument("--cur-bucket", default="")
    p.add_argument("--cur-prefix", default="")
    p.add_argument("--iceberg-database", default="ai_finops")
    p.add_argument("--iceberg-table", default="invocations")
    p.add_argument("--athena-output", required=True)
    p.add_argument("--staging-bucket", required=True)
    p.add_argument("--region", default=None)
    p.add_argument("--account-id", default="")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    run(
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        bedrock_log_bucket=args.bedrock_log_bucket,
        bedrock_log_prefix=args.bedrock_log_prefix,
        cur_bucket=args.cur_bucket,
        cur_prefix=args.cur_prefix,
        iceberg_database=args.iceberg_database,
        iceberg_table=args.iceberg_table,
        athena_output=args.athena_output,
        staging_bucket=args.staging_bucket,
        region=args.region,
        account_id=args.account_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
