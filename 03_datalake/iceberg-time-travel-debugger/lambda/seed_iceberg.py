"""Seed Lambda: creates an Iceberg table via Athena and seeds two snapshot batches.

Uses Athena engine v3 with the iceberg-debugger workgroup.
Invoked manually after deploy to populate the demo dataset.
"""

import os
import time
import boto3

ATHENA_DB = os.environ["ATHENA_DB"]
ATHENA_WORKGROUP = os.environ["ATHENA_WORKGROUP"]
TABLE_BUCKET = os.environ["TABLE_BUCKET"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

athena = boto3.client("athena", region_name=REGION)


def run_query(sql: str, database: str = None) -> str:
    kwargs = dict(
        QueryString=sql,
        WorkGroup=ATHENA_WORKGROUP,
    )
    if database:
        kwargs["QueryExecutionContext"] = {"Database": database}
    resp = athena.start_query_execution(**kwargs)
    qid = resp["QueryExecutionId"]
    for _ in range(120):
        time.sleep(2)
        st = athena.get_query_execution(QueryExecutionId=qid)
        state = st["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = st["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Query {qid} {state}: {reason}")
    raise TimeoutError(f"Query {qid} timed out")


def fetch_rows(qid: str) -> list:
    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
    rows = []
    header = None
    for page in pages:
        for row in page["ResultSet"]["Rows"]:
            vals = [c.get("VarCharValue", "") for c in row["Data"]]
            if header is None:
                header = vals
            else:
                rows.append(dict(zip(header, vals)))
    return rows


def lambda_handler(event, context):
    table_location = f"s3://{TABLE_BUCKET}/iceberg/permits/"

    # Athena Iceberg CREATE TABLE uses unquoted names + QueryExecutionContext for db
    # Athena requires explicit lengths for VARCHAR; use STRING instead (Hive/Iceberg alias)
    create_sql = f"""
CREATE TABLE IF NOT EXISTS permits (
    permit_id   STRING,
    address     STRING,
    permit_type STRING,
    status      STRING,
    issued_date DATE,
    fee_usd     DOUBLE
)
LOCATION '{table_location}'
TBLPROPERTIES (
    'table_type' = 'ICEBERG',
    'format'     = 'parquet',
    'write_compression' = 'snappy'
)
"""
    run_query(create_sql.strip(), database=ATHENA_DB)

    # Insert batch 1 — snapshot 1
    insert1 = """
INSERT INTO permits VALUES
  ('P001', '123 Main St',    'BUILDING',    'ISSUED',    DATE '2024-01-10', 250.00),
  ('P002', '456 Oak Ave',    'ELECTRICAL',  'ISSUED',    DATE '2024-01-11', 100.00),
  ('P003', '789 Pine Rd',    'PLUMBING',    'PENDING',   DATE '2024-01-12',  75.00),
  ('P004', '321 Elm St',     'BUILDING',    'CANCELLED', DATE '2024-01-13', 250.00),
  ('P005', '654 Maple Dr',   'MECHANICAL',  'ISSUED',    DATE '2024-01-14', 180.00)
"""
    run_query(insert1.strip(), database=ATHENA_DB)

    # Insert batch 2 — snapshot 2 (new rows)
    insert2 = """
INSERT INTO permits VALUES
  ('P006', '999 Cedar Ln',   'BUILDING',    'ISSUED',    DATE '2024-02-01', 300.00),
  ('P007', '888 Birch Blvd', 'ELECTRICAL',  'PENDING',   DATE '2024-02-03',  90.00),
  ('P008', '777 Walnut Way', 'BUILDING',    'ISSUED',    DATE '2024-02-05', 420.00)
"""
    run_query(insert2.strip(), database=ATHENA_DB)

    # List snapshots to confirm two were created
    snap_qid = run_query(
        'SELECT snapshot_id, committed_at FROM "permits$snapshots" ORDER BY committed_at',
        database=ATHENA_DB,
    )
    snapshots = fetch_rows(snap_qid)

    return {
        "statusCode": 200,
        "table": f"{ATHENA_DB}.permits",
        "location": table_location,
        "snapshots": snapshots,
    }
