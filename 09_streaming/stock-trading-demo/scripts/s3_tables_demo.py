#!/usr/bin/env python3
"""
S3 Tables + Iceberg classroom demo runner.

Executes the curated SQL examples in ../queries/ against Athena and prints the
results in a console-friendly format. Each query file may contain several
statements separated by `-- 1.` / `-- 2.` headers; we split on the headers
and run them one at a time so you can see what each one produces.

Usage:
    python3 scripts/s3_tables_demo.py             # run all sections
    python3 scripts/s3_tables_demo.py time_travel # run one section
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import boto3

GLUE_DATABASE = "trading"
GLUE_TABLE = "trades"
QUERIES_DIR = Path(__file__).resolve().parent.parent / "queries"
SECTIONS = ("aggregations", "time_travel", "schema_evolution")


def get_query_results_bucket() -> str:
    cf = boto3.client("cloudformation")
    outputs = cf.describe_stacks(StackName="StockTradingStack")["Stacks"][0]["Outputs"]
    for output in outputs:
        if output["OutputKey"] == "S3BucketName":
            return output["OutputValue"]
    raise RuntimeError("S3BucketName output not found on StockTradingStack")


def split_statements(sql_text: str) -> list[tuple[str, str]]:
    """Split a queries/*.sql file into (label, statement) pairs.

    The files use a `-- N. <description>` line to separate examples. We treat
    each numbered block as a single statement and skip ones that are entirely
    commented out (so the optional examples in schema_evolution.sql don't run
    by accident).
    """
    blocks = re.split(r"(?m)^-- \d+\.\s", sql_text)
    pairs: list[tuple[str, str]] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        first_line, _, rest = block.partition("\n")
        label = first_line.strip()
        body = rest.strip() if rest else ""
        if not body:
            continue
        # Strip pure-comment statements (every non-blank line begins with `--`).
        non_comment = [
            ln for ln in body.splitlines()
            if ln.strip() and not ln.lstrip().startswith("--")
        ]
        if not non_comment:
            continue
        pairs.append((label, "\n".join(non_comment).rstrip(";").strip()))
    return pairs


def run_query(athena, sql: str, results_bucket: str) -> dict:
    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": GLUE_DATABASE},
        ResultConfiguration={"OutputLocation": f"s3://{results_bucket}/query-results/"},
    )
    qid = response["QueryExecutionId"]

    while True:
        execution = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return execution
        time.sleep(1)


def print_results(athena, query_execution_id: str, max_rows: int = 10) -> None:
    results = athena.get_query_results(
        QueryExecutionId=query_execution_id, MaxResults=max_rows + 1
    )
    rows = results["ResultSet"]["Rows"]
    if not rows:
        print("   (no rows)")
        return
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
    widths = [max(len(h), 8) for h in headers]
    for row in rows[1:]:
        for i, col in enumerate(row["Data"]):
            widths[i] = max(widths[i], len(col.get("VarCharValue", "") or ""))
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    print("   " + fmt.format(*headers))
    print("   " + "-+-".join("-" * w for w in widths))
    for row in rows[1:]:
        values = [col.get("VarCharValue", "") or "" for col in row["Data"]]
        print("   " + fmt.format(*values))


def run_section(section: str, athena, results_bucket: str) -> None:
    path = QUERIES_DIR / f"{section}.sql"
    if not path.exists():
        print(f"❌ Missing {path}")
        return
    print(f"\n{'=' * 70}\n📂 {section}.sql\n{'=' * 70}")
    statements = split_statements(path.read_text())
    if not statements:
        print("   (no executable statements — every example is commented out)")
        return
    for idx, (label, sql) in enumerate(statements, start=1):
        print(f"\n{idx}. {label}")
        print(f"   {sql.splitlines()[0][:90]}{'...' if len(sql.splitlines()[0]) > 90 else ''}")
        execution = run_query(athena, sql, results_bucket)
        state = execution["Status"]["State"]
        if state != "SUCCEEDED":
            reason = execution["Status"].get("StateChangeReason", "no reason given")
            print(f"   ❌ {state}: {reason}")
            continue
        print_results(athena, execution["QueryExecutionId"])
        scanned_mb = execution.get("Statistics", {}).get("DataScannedInBytes", 0) / (1024 * 1024)
        runtime_ms = execution.get("Statistics", {}).get("EngineExecutionTimeInMillis", 0)
        print(f"   ✓ scanned {scanned_mb:.2f} MB in {runtime_ms} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "section",
        nargs="?",
        choices=SECTIONS,
        help="Run only one section instead of all of them.",
    )
    args = parser.parse_args()

    print("🚀 S3 Tables + Iceberg query demo")
    print(f"   database: {GLUE_DATABASE}")
    print(f"   table:    {GLUE_TABLE}")

    try:
        results_bucket = get_query_results_bucket()
    except Exception as exc:
        print(f"❌ Could not resolve query results bucket: {exc}")
        return 1
    print(f"   results:  s3://{results_bucket}/query-results/")

    athena = boto3.client("athena")
    sections = (args.section,) if args.section else SECTIONS
    for section in sections:
        run_section(section, athena, results_bucket)
    print("\n🎉 Demo complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
