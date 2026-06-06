#!/usr/bin/env python3
"""
Populate iceberg_maintenance_test.maintenance_test via Athena INSERT INTO.
Each INSERT creates a separate snapshot and a small file — exactly what
the maintenance agent should detect and fix.
"""
import boto3
import os
import time
import sys

REGION = "us-east-1"
# Athena query-results bucket. Per-account; override with env var so the
# script never carries a hardcoded account-scoped name.
RESULTS_BUCKET = os.environ.get("ATHENA_RESULTS_BUCKET")
if not RESULTS_BUCKET:
    sys.exit(
        "Set ATHENA_RESULTS_BUCKET to the Athena query-results bucket for this account "
        "(e.g. <account-id>-athena-query-results)."
    )
TABLE_S3_LOCATION = os.environ.get(
    "MAINTENANCE_TABLE_S3_LOCATION",
    "s3://apache-iceberg-us-east-1-<account-id>/iceberg/maintenance_test/",
)
RESULTS_PREFIX = "iceberg-agent-test"
NUM_INSERTS = 25   # 25 snapshots, 25 tiny files

athena = boto3.client("athena", region_name=REGION)


def run_query(sql: str, description: str = "") -> str:
    resp = athena.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": f"s3://{RESULTS_BUCKET}/{RESULTS_PREFIX}/"},
    )
    qid = resp["QueryExecutionId"]
    for _ in range(60):
        time.sleep(3)
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "")
            raise RuntimeError(f"Query {description!r} {state}: {reason}\nSQL: {sql}")
    raise TimeoutError(f"Query timed out: {description}")


def main():
    print(f"Inserting {NUM_INSERTS} small batches into maintenance_test...")
    for i in range(NUM_INSERTS):
        # 20 rows per batch — intentionally tiny
        rows = ", ".join(
            f"('{i*20+j:06d}', {i*20+j}, "
            f"'{['click','view','purchase'][j%3]}', {float(i*100+j):.2f}, "
            f"TIMESTAMP '2026-01-{(i%28)+1:02d} 12:00:00')"
            for j in range(20)
        )
        sql = f"""INSERT INTO iceberg_maintenance_test.maintenance_test
(event_id, user_id, event_type, value, event_ts)
VALUES {rows}"""
        run_query(sql, f"insert batch {i+1}")
        if (i + 1) % 5 == 0:
            print(f"  Inserted batch {i+1}/{NUM_INSERTS}")

    # Verify via SELECT COUNT
    qid = run_query(
        "SELECT COUNT(*) as cnt FROM iceberg_maintenance_test.maintenance_test",
        "count"
    )
    result = athena.get_query_results(QueryExecutionId=qid)
    count = result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"]
    print(f"\nTotal rows: {count}")
    print(f"Snapshots created: ~{NUM_INSERTS} (one per INSERT)")
    print("\nTest table is ready.")
    print(f"Table S3 location: {TABLE_S3_LOCATION}")


if __name__ == "__main__":
    main()
