#!/usr/bin/env python3
"""schema-evolution-simulator

Chaos-engineering tool that runs a catalog of safe, unsafe, and chaos Iceberg
schema mutations against a live Athena/Glue Iceberg table, validates how each
mutation behaves, and prints a detailed results report.

Usage:
    python simulator.py --table iceberg_schema_sim.orders \\
                        --workgroup iceberg-schema-sim \\
                        [--run safe]          # run only safe mutations
                        [--run unsafe]        # run only unsafe mutations
                        [--run chaos]         # run only chaos mutations
                        [--run all]           # run everything (default)
                        [--run <name>]        # run a single named mutation
                        [--list]              # list all mutations and exit
                        [--explain <name>]    # show full description for one mutation
                        [--dry-run]           # print DDL without executing
                        [--no-reset]          # skip table reset between mutations
                        [--region us-west-2]
                        [--format table|json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import boto3

from mutations import MUTATIONS, ALL_NAMES, SAFE_NAMES, UNSAFE_NAMES, CHAOS_NAMES

# ── Athena runner ──────────────────────────────────────────────────────────────

class AthenaRunner:
    def __init__(self, workgroup: str, output_location: str, region: str):
        self.client = boto3.client("athena", region_name=region)
        self.workgroup = workgroup
        self.output_location = output_location

    def run(self, sql: str, database: str = None) -> tuple[bool, str, list[dict]]:
        """Execute SQL. Returns (success, error_reason, rows)."""
        kwargs = dict(QueryString=sql, WorkGroup=self.workgroup)
        if database:
            kwargs["QueryExecutionContext"] = {"Database": database}
        try:
            resp = self.client.start_query_execution(**kwargs)
        except Exception as e:
            return False, str(e), []

        qid = resp["QueryExecutionId"]
        for _ in range(150):
            time.sleep(2)
            st = self.client.get_query_execution(QueryExecutionId=qid)
            state = st["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                rows = self._fetch(qid)
                return True, "", rows
            if state in ("FAILED", "CANCELLED"):
                reason = st["QueryExecution"]["Status"].get("StateChangeReason", state)
                return False, reason, []
        return False, "TIMEOUT", []

    def _fetch(self, qid: str) -> list[dict]:
        rows, header = [], None
        paginator = self.client.get_paginator("get_query_results")
        for page in paginator.paginate(QueryExecutionId=qid):
            for row in page["ResultSet"]["Rows"]:
                vals = [c.get("VarCharValue", "") for c in row["Data"]]
                if header is None:
                    header = vals
                else:
                    rows.append(dict(zip(header, vals)))
        return rows


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class MutationResult:
    name: str
    category: str
    title: str
    ddl_ok: bool
    ddl_expected_ok: bool
    ddl_error: str
    validation_ok: bool
    validation_rows: int
    expected_rows: Optional[int]
    duration_s: float
    consumer_notes: dict
    iceberg_note: str
    verdict: str = field(init=False)

    def __post_init__(self):
        # DDL matched expectation AND validation matched expectation
        ddl_match = self.ddl_ok == self.ddl_expected_ok
        val_match = (
            self.expected_rows is None  # chaos: we just observe
            or (self.validation_ok and self.validation_rows >= self.expected_rows)
        )
        if ddl_match and val_match:
            self.verdict = "PASS"
        elif not ddl_match:
            self.verdict = "DDL_UNEXPECTED"
        else:
            self.verdict = "VALIDATION_UNEXPECTED"


# ── Table reset ────────────────────────────────────────────────────────────────

BASELINE_DDL = """\
CREATE TABLE IF NOT EXISTS {db}.{table} (
    permit_id   STRING,
    address     STRING,
    permit_type STRING,
    status      STRING,
    issued_date DATE,
    fee_usd     DOUBLE,
    record_count INT
)
PARTITIONED BY (permit_type)
LOCATION '{location}'
TBLPROPERTIES (
    'table_type'        = 'ICEBERG',
    'format'            = 'parquet',
    'write_compression' = 'snappy'
)"""

BASELINE_INSERT = """\
INSERT INTO {db}.{table} VALUES
  ('P001','123 Main St',   'BUILDING',   'ISSUED',    DATE '2024-01-10', 250.00, 1),
  ('P002','456 Oak Ave',   'ELECTRICAL', 'ISSUED',    DATE '2024-01-11', 100.00, 2),
  ('P003','789 Pine Rd',   'PLUMBING',   'PENDING',   DATE '2024-01-12',  75.00, 3),
  ('P004','321 Elm St',    'BUILDING',   'CANCELLED', DATE '2024-01-13', 250.00, 4),
  ('P005','654 Maple Dr',  'MECHANICAL', 'ISSUED',    DATE '2024-01-14', 180.00, 5),
  ('P006','999 Cedar Ln',  'BUILDING',   'ISSUED',    DATE '2024-02-01', 300.00, 6),
  ('P007','888 Birch Blvd','ELECTRICAL', 'PENDING',   DATE '2024-02-03',  90.00, 7),
  ('P008','777 Walnut Way','BUILDING',   'ISSUED',    DATE '2024-02-05', 420.00, 8)"""


def reset_table(athena: AthenaRunner, db: str, table: str, location: str,
                verbose: bool = False) -> bool:
    """Drop and recreate the table at baseline schema with 8 seed rows."""
    steps = [
        (f"DROP TABLE IF EXISTS {db}.{table}", db),
        (BASELINE_DDL.format(db=db, table=table, location=location), db),
        (BASELINE_INSERT.format(db=db, table=table), db),
    ]
    for sql, database in steps:
        ok, err, _ = athena.run(sql, database=database)
        if not ok:
            if verbose:
                print(f"  [reset error] {err}")
            return False
    return True


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_mutation(
    athena: AthenaRunner,
    mutation: dict,
    db: str,
    table: str,
    location: str,
    dry_run: bool = False,
    reset: bool = True,
    verbose: bool = False,
) -> MutationResult:
    name = mutation["name"]
    category = mutation["category"]

    if verbose:
        print(f"  {'[DRY-RUN] ' if dry_run else ''}Running: {mutation['title']}")

    # Reset table to baseline before each mutation
    if reset:
        if verbose:
            print("    Resetting table to baseline...")
        if not dry_run:
            ok = reset_table(athena, db, table, location, verbose=verbose)
            if not ok:
                return MutationResult(
                    name=name, category=category, title=mutation["title"],
                    ddl_ok=False, ddl_expected_ok=mutation["expect_ddl_ok"],
                    ddl_error="Table reset failed",
                    validation_ok=False, validation_rows=0,
                    expected_rows=mutation.get("expect_rows"),
                    duration_s=0.0,
                    consumer_notes=mutation.get("consumer_notes", {}),
                    iceberg_note=mutation.get("iceberg_note", ""),
                )

    t0 = time.time()

    # Execute DDL
    ddl = mutation.get("ddl")
    ddl_ok, ddl_err = True, ""
    if ddl and not dry_run:
        sql = ddl.format(db=db, table=table)
        if verbose:
            print(f"    DDL: {sql[:80]}...")
        ddl_ok, ddl_err, _ = athena.run(sql, database=db)
    elif ddl and dry_run:
        sql = ddl.format(db=db, table=table)
        if verbose:
            print(f"    [DRY] DDL: {sql}")

    # Run validation
    val_sql = mutation.get("validation_sql", "")
    val_ok, val_err, val_rows = True, "", []
    if val_sql and not dry_run:
        # Only run validation if DDL succeeded or DDL was expected to fail
        # (in chaos mode we always try the validation to observe the failure)
        sql = val_sql.format(db=db, table=table)
        val_ok, val_err, val_rows = athena.run(sql, database=db)
        if verbose:
            status = "OK" if val_ok else f"FAILED: {val_err[:60]}"
            print(f"    Validation: {status} ({len(val_rows)} rows)")

    duration = time.time() - t0

    return MutationResult(
        name=name,
        category=category,
        title=mutation["title"],
        ddl_ok=ddl_ok,
        ddl_expected_ok=mutation["expect_ddl_ok"],
        ddl_error=ddl_err[:120] if ddl_err else "",
        validation_ok=val_ok,
        validation_rows=len(val_rows),
        expected_rows=mutation.get("expect_rows"),
        duration_s=round(duration, 1),
        consumer_notes=mutation.get("consumer_notes", {}),
        iceberg_note=mutation.get("iceberg_note", ""),
    )


# ── Formatters ─────────────────────────────────────────────────────────────────

VERDICT_SYMBOL = {
    "PASS": "✓",
    "DDL_UNEXPECTED": "✗",
    "VALIDATION_UNEXPECTED": "~",
}
VERDICT_LABEL = {
    "PASS": "PASS",
    "DDL_UNEXPECTED": "UNEXPECTED DDL",
    "VALIDATION_UNEXPECTED": "UNEXPECTED VAL",
}
CATEGORY_LABEL = {"safe": "SAFE  ", "unsafe": "UNSAFE", "chaos": "CHAOS "}


def format_table(results: list[MutationResult]) -> str:
    lines = []
    sep = "=" * 90
    lines.append(sep)
    lines.append(f"  SCHEMA EVOLUTION SIMULATOR — {len(results)} mutations — "
                 f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(sep)

    by_cat = {"safe": [], "unsafe": [], "chaos": []}
    for r in results:
        by_cat[r.category].append(r)

    for cat in ("safe", "unsafe", "chaos"):
        cat_results = by_cat[cat]
        if not cat_results:
            continue
        lines.append(f"\n── {cat.upper()} mutations ({'all passed' if all(r.verdict == 'PASS' for r in cat_results) else 'some unexpected'}) ──")
        lines.append(f"{'#':<3} {'Category':<8} {'Name':<30} {'DDL':<7} {'Val':<7} {'Rows':<6} {'Verdict':<18} {'Time':>5}")
        lines.append("-" * 90)
        for i, r in enumerate(cat_results, 1):
            ddl_sym = "OK " if r.ddl_ok else "FAIL"
            val_sym = "OK " if r.validation_ok else "FAIL"
            rows_str = str(r.validation_rows)
            verdict_str = f"{VERDICT_SYMBOL.get(r.verdict, '?')} {VERDICT_LABEL.get(r.verdict, r.verdict)}"
            lines.append(
                f"{i:<3} {CATEGORY_LABEL[cat]} {r.name:<30} {ddl_sym:<7} {val_sym:<7} "
                f"{rows_str:<6} {verdict_str:<18} {r.duration_s:>4.1f}s"
            )
            if r.ddl_error:
                lines.append(f"    DDL error: {r.ddl_error[:80]}")

    # Summary
    passed = sum(1 for r in results if r.verdict == "PASS")
    lines.append(f"\n{'─'*90}")
    lines.append(f"  Results: {passed}/{len(results)} mutations behaved as expected")
    lines.append(f"{'─'*90}")

    # Consumer notes section
    lines.append("\n── Consumer behaviour notes ──")
    for r in results:
        lines.append(f"\n  [{r.category.upper()}] {r.name}: {r.title}")
        lines.append(f"  Iceberg: {r.iceberg_note}")
        for consumer, note in r.consumer_notes.items():
            lines.append(f"    {consumer:<22} {note}")

    return "\n".join(lines)


def format_json(results: list[MutationResult]) -> str:
    out = []
    for r in results:
        out.append({
            "name": r.name,
            "category": r.category,
            "title": r.title,
            "verdict": r.verdict,
            "ddl_ok": r.ddl_ok,
            "ddl_expected_ok": r.ddl_expected_ok,
            "ddl_error": r.ddl_error,
            "validation_ok": r.validation_ok,
            "validation_rows": r.validation_rows,
            "expected_rows": r.expected_rows,
            "duration_s": r.duration_s,
            "iceberg_note": r.iceberg_note,
            "consumer_notes": r.consumer_notes,
        })
    return json.dumps(out, indent=2)


# ── CLI ────────────────────────────────────────────────────────────────────────

def resolve_output_location(output_bucket: Optional[str], workgroup: str, region: str) -> str:
    if output_bucket:
        return output_bucket.rstrip("/") + "/"
    client = boto3.client("athena", region_name=region)
    try:
        wg = client.get_work_group(WorkGroup=workgroup)
        loc = wg["WorkGroup"]["Configuration"].get("ResultConfiguration", {}).get("OutputLocation")
        if loc:
            return loc
    except Exception:
        pass
    raise ValueError(
        "Cannot determine Athena output location. "
        "Pass --output-bucket or use a workgroup with a configured result location."
    )


def resolve_region(region: Optional[str]) -> str:
    if region:
        return region
    return boto3.session.Session().region_name or "us-east-1"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simulator",
        description="Iceberg schema evolution chaos simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all mutations
  python simulator.py --table iceberg_schema_sim.orders --workgroup iceberg-schema-sim

  # Run only safe mutations
  python simulator.py --table iceberg_schema_sim.orders --run safe

  # Run a single named mutation
  python simulator.py --table iceberg_schema_sim.orders --run rename_column

  # Dry-run: print DDL without executing
  python simulator.py --table iceberg_schema_sim.orders --run all --dry-run

  # List all available mutations
  python simulator.py --list

  # Explain a mutation
  python simulator.py --explain rename_column
""",
    )
    p.add_argument("--table", default=None,
                   help="Fully-qualified table: <database>.<table>")
    p.add_argument("--table-location", default=None,
                   help="S3 location for Iceberg table data, e.g. s3://bucket/prefix/")
    p.add_argument("--workgroup", default="iceberg-schema-sim",
                   help="Athena workgroup (default: iceberg-schema-sim)")
    p.add_argument("--output-bucket", default=None,
                   help="Athena results S3 location override")
    p.add_argument("--region", default=None)
    p.add_argument("--run", default="all",
                   help="Mutations to run: all | safe | unsafe | chaos | <name>")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--dry-run", action="store_true",
                   help="Print SQL without executing")
    p.add_argument("--no-reset", action="store_true",
                   help="Skip table reset between mutations (run in sequence)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--list", action="store_true",
                   help="List all available mutations and exit")
    p.add_argument("--explain", metavar="NAME",
                   help="Print full description for a named mutation and exit")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    # --list mode
    if args.list:
        print(f"\n{'Name':<35} {'Category':<10} Title")
        print("-" * 90)
        for m in MUTATIONS:
            print(f"{m['name']:<35} {m['category']:<10} {m['title']}")
        return

    # --explain mode
    if args.explain:
        name = args.explain
        match = next((m for m in MUTATIONS if m["name"] == name), None)
        if not match:
            print(f"Unknown mutation: {name}")
            print(f"Available: {', '.join(ALL_NAMES)}")
            sys.exit(1)
        print(f"\n{'='*70}")
        print(f"  {match['title']}")
        print(f"  Category: {match['category'].upper()}")
        print(f"{'='*70}")
        print(f"\n{match['description']}")
        print(f"\nIceberg note:\n  {match['iceberg_note']}")
        print("\nExpected DDL outcome:", "SUCCESS" if match["expect_ddl_ok"] else "FAILURE (expected to be rejected)")
        if match.get("ddl"):
            print(f"\nDDL:\n  {match['ddl']}")
        print("\nConsumer behaviour:")
        for consumer, note in match.get("consumer_notes", {}).items():
            print(f"  {consumer:<22} {note}")
        return

    # Require --table for run mode
    if not args.table:
        parser.error("--table is required (e.g. --table iceberg_schema_sim.orders)")
    if "." not in args.table:
        parser.error("--table must be <database>.<table>")

    db, table = args.table.split(".", 1)
    region = resolve_region(args.region)
    output_location = resolve_output_location(args.output_bucket, args.workgroup, region)

    # Require table location for reset
    table_location = args.table_location
    if not table_location and not args.no_reset and not args.dry_run:
        # Auto-discover from workgroup output bucket
        bucket = output_location.replace("s3://", "").split("/")[0]
        table_location = f"s3://{bucket}/iceberg/{table}/"

    athena = AthenaRunner(args.workgroup, output_location, region)

    # Select mutations
    run_arg = args.run.lower()
    if run_arg == "all":
        selected = MUTATIONS
    elif run_arg == "safe":
        selected = [m for m in MUTATIONS if m["category"] == "safe"]
    elif run_arg == "unsafe":
        selected = [m for m in MUTATIONS if m["category"] == "unsafe"]
    elif run_arg == "chaos":
        selected = [m for m in MUTATIONS if m["category"] == "chaos"]
    else:
        match = next((m for m in MUTATIONS if m["name"] == run_arg), None)
        if not match:
            parser.error(f"Unknown --run value '{run_arg}'. Use: all|safe|unsafe|chaos|<name>")
        selected = [match]

    print(f"\nSchema Evolution Simulator")
    print(f"Table:     {db}.{table}")
    print(f"Workgroup: {args.workgroup}")
    print(f"Mutations: {len(selected)} selected ({args.run})")
    print(f"Reset:     {'no' if args.no_reset else 'yes (table reset to baseline before each mutation)'}")
    if args.dry_run:
        print("Mode:      DRY-RUN (no queries executed)")
    print()

    results = []
    for i, mutation in enumerate(selected, 1):
        print(f"[{i}/{len(selected)}] {mutation['category'].upper():<6} {mutation['name']}", flush=True)
        result = run_mutation(
            athena=athena,
            mutation=mutation,
            db=db,
            table=table,
            location=table_location or "",
            dry_run=args.dry_run,
            reset=not args.no_reset,
            verbose=args.verbose,
        )
        results.append(result)
        verdict = result.verdict
        sym = VERDICT_SYMBOL.get(verdict, "?")
        print(f"         {sym} {VERDICT_LABEL.get(verdict, verdict)} "
              f"(DDL={'OK' if result.ddl_ok else 'FAIL'}, "
              f"rows={result.validation_rows}, {result.duration_s:.1f}s)")

    print()
    if args.format == "json":
        print(format_json(results))
    else:
        print(format_table(results))


if __name__ == "__main__":
    main()
