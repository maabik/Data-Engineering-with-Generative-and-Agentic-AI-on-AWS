"""Seed Lambda: creates the Iceberg orders table and inserts baseline rows.

Creates: iceberg_schema_sim.orders
Columns: permit_id, address, permit_type, status, issued_date, fee_usd, record_count
Partitioned by: permit_type

Invoked manually after deploy. The simulator CLI also resets the table
before each mutation run, so this is only needed for first-time setup.
"""

import os
import re
import time
import boto3

# Validate every env-sourced identifier that ends up interpolated into raw
# Athena SQL. A poisoned env var (e.g. parameter-store compromise, SSRF that
# can rewrite Lambda config) would otherwise inject DDL/DML into queries that
# this Lambda runs with workgroup-level privileges.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


def _safe_ident(name: str, value: str, pattern: re.Pattern) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name}={value!r} fails identifier validation")
    return value


ATHENA_DB = _safe_ident("ATHENA_DB", os.environ["ATHENA_DB"], _IDENT_RE)
ATHENA_WORKGROUP = _safe_ident("ATHENA_WORKGROUP", os.environ["ATHENA_WORKGROUP"], _IDENT_RE)
TABLE_BUCKET = _safe_ident("TABLE_BUCKET", os.environ["TABLE_BUCKET"], _BUCKET_RE)
TABLE_NAME = _safe_ident("TABLE_NAME", "orders", _IDENT_RE)
REGION = os.environ.get("AWS_REGION", "us-east-1")

athena = boto3.client("athena", region_name=REGION)


def run_query(sql: str, database: str = None) -> dict:
    kwargs = dict(QueryString=sql, WorkGroup=ATHENA_WORKGROUP)
    if database:
        kwargs["QueryExecutionContext"] = {"Database": database}
    resp = athena.start_query_execution(**kwargs)
    qid = resp["QueryExecutionId"]
    for _ in range(150):
        time.sleep(2)
        st = athena.get_query_execution(QueryExecutionId=qid)
        state = st["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return {"ok": True, "qid": qid}
        if state in ("FAILED", "CANCELLED"):
            reason = st["QueryExecution"]["Status"].get("StateChangeReason", state)
            raise RuntimeError(f"Query {qid} {state}: {reason}")
    raise TimeoutError(f"Query {qid} timed out")


def lambda_handler(event, context):
    location = f"s3://{TABLE_BUCKET}/iceberg/{TABLE_NAME}/"

    run_query(f"DROP TABLE IF EXISTS {TABLE_NAME}", database=ATHENA_DB)

    run_query(f"""
CREATE TABLE {TABLE_NAME} (
    permit_id    STRING,
    address      STRING,
    permit_type  STRING,
    status       STRING,
    issued_date  DATE,
    fee_usd      DOUBLE,
    record_count INT
)
PARTITIONED BY (permit_type)
LOCATION '{location}'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'parquet',
    'write_compression' = 'snappy'
)""", database=ATHENA_DB)

    run_query(f"""
INSERT INTO {TABLE_NAME} VALUES
  ('P001','123 Main St',   'BUILDING',   'ISSUED',    DATE '2024-01-10', 250.00, 1),
  ('P002','456 Oak Ave',   'ELECTRICAL', 'ISSUED',    DATE '2024-01-11', 100.00, 2),
  ('P003','789 Pine Rd',   'PLUMBING',   'PENDING',   DATE '2024-01-12',  75.00, 3),
  ('P004','321 Elm St',    'BUILDING',   'CANCELLED', DATE '2024-01-13', 250.00, 4),
  ('P005','654 Maple Dr',  'MECHANICAL', 'ISSUED',    DATE '2024-01-14', 180.00, 5),
  ('P006','999 Cedar Ln',  'BUILDING',   'ISSUED',    DATE '2024-02-01', 300.00, 6),
  ('P007','888 Birch Blvd','ELECTRICAL', 'PENDING',   DATE '2024-02-03',  90.00, 7),
  ('P008','777 Walnut Way','BUILDING',   'ISSUED',    DATE '2024-02-05', 420.00, 8)
""", database=ATHENA_DB)

    return {
        "statusCode": 200,
        "table": f"{ATHENA_DB}.{TABLE_NAME}",
        "location": location,
        "rows": 8,
    }
