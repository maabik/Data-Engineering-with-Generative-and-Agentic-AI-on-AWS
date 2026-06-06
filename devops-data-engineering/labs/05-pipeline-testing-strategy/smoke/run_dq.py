#!/usr/bin/env python3
"""Run the YAML expectations from contracts/expectations/ against the
curated table via Athena. Exit non-zero on any failure so a CI step can
trigger rollback.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import boto3
import yaml


def query(athena, db: str, output_loc: str, sql: str) -> int:
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": db},
        ResultConfiguration={"OutputLocation": output_loc},
    )["QueryExecutionId"]
    deadline = time.time() + 60
    while time.time() < deadline:
        s = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if s in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(2)
    if s != "SUCCEEDED":
        raise RuntimeError(f"Athena query failed: {sql}")
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    return int(rows[1]["Data"][0]["VarCharValue"])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--contract", required=True)
    p.add_argument("--database", required=True)
    p.add_argument("--output-location", required=True)
    args = p.parse_args()

    contract = yaml.safe_load(Path(args.contract).read_text())
    table = contract["table"]
    athena = boto3.client("athena")

    failures = []
    for exp in contract["expectations"]:
        ((name, body),) = exp.items()
        if name == "expect_table_row_count_to_be_between":
            n = query(athena, args.database, args.output_location, f"SELECT count(*) FROM {table}")
            if not (body["min_value"] <= n <= body["max_value"]):
                failures.append(f"row count {n} not in [{body['min_value']}, {body['max_value']}]")
        elif name == "expect_column_values_to_not_be_null":
            col = body["column"]
            n = query(athena, args.database, args.output_location, f"SELECT count(*) FROM {table}")
            nn = query(athena, args.database, args.output_location, f"SELECT count(*) FROM {table} WHERE {col} IS NOT NULL")
            ratio = (nn / n) if n else 1.0
            if ratio < body["mostly"]:
                failures.append(f"non-null rate on {col} is {ratio:.4f} < {body['mostly']}")
        elif name == "expect_column_values_to_be_between":
            col = body["column"]
            outside = query(athena, args.database, args.output_location,
                f"SELECT count(*) FROM {table} WHERE {col} < {body['min_value']}")
            if outside > 0:
                failures.append(f"{outside} rows have {col} < {body['min_value']}")
        elif name == "expect_column_unique_value_count_to_be_between":
            col = body["column"]
            distinct = query(athena, args.database, args.output_location, f"SELECT count(distinct {col}) FROM {table}")
            if not (body["min_value"] <= distinct <= body["max_value"]):
                failures.append(f"distinct {col} = {distinct} not in [{body['min_value']}, {body['max_value']}]")
        else:
            failures.append(f"unsupported expectation type: {name}")

    if failures:
        print("DQ FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("DQ OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
