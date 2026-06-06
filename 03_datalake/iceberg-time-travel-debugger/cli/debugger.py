#!/usr/bin/env python3
"""iceberg-time-travel-debugger

CLI that diffs two Iceberg snapshots and produces a row-level changelog.

Usage:
    python debugger.py --table <glue-db>.<table> \\
                       --from "yesterday 5pm" \\
                       --to "now" \\
                       [--workgroup medallion] \\
                       [--output-bucket s3://bucket/prefix/] \\
                       [--region us-west-2] \\
                       [--format table|json|csv]

The tool:
  1. Resolves natural-language timestamps to UTC datetimes
  2. Queries Iceberg snapshot history via Athena (iceberg_snapshots table function)
  3. Finds the closest snapshot_id at or before each timestamp
  4. Runs Athena EXCEPT queries using FOR SYSTEM_TIME AS OF to diff snapshots
  5. Prints a row-level changelog: ADDED / REMOVED rows
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
import re


# ── Natural-language timestamp parser ─────────────────────────────────────────

def parse_natural_time(expr: str, now: Optional[datetime] = None) -> datetime:
    """Parse natural-language time expressions to UTC datetime.

    Supports:
      - ISO 8601: "2024-03-15T17:00:00"
      - "now"
      - "today [HH:MM|H am/pm]"
      - "yesterday [HH:MM|H am/pm]"
      - "N hours ago", "N minutes ago", "N days ago"
      - "YYYY-MM-DD HH:MM"
    """
    if now is None:
        now = datetime.now(timezone.utc)

    expr = expr.strip().lower()

    if expr in ("now", "current", "today"):
        return now

    # ISO 8601 with optional timezone
    try:
        dt = datetime.fromisoformat(expr.replace("z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # "N units ago"
    m = re.match(r"(\d+)\s*(second|minute|hour|day|week)s?\s+ago", expr)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        deltas = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
        return now - timedelta(seconds=n * deltas[unit])

    # "yesterday [time]" / "today [time]"
    m = re.match(r"(yesterday|today)(?:\s+(.+))?", expr)
    if m:
        base_word, time_part = m.group(1), m.group(2)
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if base_word == "yesterday":
            base -= timedelta(days=1)
        if time_part:
            base = _apply_time_part(base, time_part.strip())
        return base

    # "YYYY-MM-DD[ HH:MM[:SS]]"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(expr, fmt).replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass

    raise ValueError(f"Cannot parse timestamp expression: '{expr}'")


def _apply_time_part(base: datetime, time_expr: str) -> datetime:
    """Apply a time-of-day expression to a date."""
    # "5pm", "5 pm", "17:00", "5:30pm"
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_expr.lower())
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        meridiem = m.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base


# ── Athena helpers ─────────────────────────────────────────────────────────────

class AthenaRunner:
    def __init__(self, workgroup: str, output_location: str, region: str):
        self.client = boto3.client("athena", region_name=region)
        self.workgroup = workgroup
        self.output_location = output_location

    def run(self, sql: str) -> list[dict]:
        """Execute SQL and return rows as list of dicts."""
        resp = self.client.start_query_execution(
            QueryString=sql,
            WorkGroup=self.workgroup,
            ResultConfiguration={"OutputLocation": self.output_location},
        )
        qid = resp["QueryExecutionId"]
        self._wait(qid)
        return self._fetch_results(qid)

    def _wait(self, qid: str, poll_sec: float = 1.0, max_wait: int = 300):
        deadline = time.time() + max_wait
        while time.time() < deadline:
            status = self.client.get_query_execution(QueryExecutionId=qid)
            state = status["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                if state != "SUCCEEDED":
                    reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
                    raise RuntimeError(f"Athena query {qid} {state}: {reason}")
                return
            time.sleep(poll_sec)
        raise TimeoutError(f"Athena query {qid} did not complete within {max_wait}s")

    def _fetch_results(self, qid: str) -> list[dict]:
        rows = []
        paginator = self.client.get_paginator("get_query_results")
        header = None
        for page in paginator.paginate(QueryExecutionId=qid):
            result_rows = page["ResultSet"]["Rows"]
            if header is None:
                header = [c["VarCharValue"] for c in result_rows[0]["Data"]]
                result_rows = result_rows[1:]
            for row in result_rows:
                values = [c.get("VarCharValue", "") for c in row["Data"]]
                rows.append(dict(zip(header, values)))
        return rows


# ── Snapshot resolution ────────────────────────────────────────────────────────

def find_snapshot_at(athena: AthenaRunner, db: str, table: str, ts: datetime) -> Optional[str]:
    """Find the latest snapshot_id committed at or before ts.

    Queries the Iceberg metadata table `<db>."<table>$snapshots"` which Athena
    exposes for Iceberg tables registered in Glue.
    """
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    sql = f"""
SELECT snapshot_id, committed_at
FROM "{db}"."{table}$snapshots"
WHERE committed_at <= TIMESTAMP '{ts_str}'
ORDER BY committed_at DESC
LIMIT 1
"""
    rows = athena.run(sql.strip())
    if not rows:
        return None
    return rows[0]["snapshot_id"]


# ── Row-level diff ─────────────────────────────────────────────────────────────

def diff_snapshots(
    athena: AthenaRunner,
    db: str,
    table: str,
    snap_from: str,
    snap_to: str,
) -> tuple[list[dict], list[dict]]:
    """Return (added_rows, removed_rows) between two snapshot IDs.

    Uses Iceberg FOR SYSTEM_VERSION AS OF syntax:
      - added   = rows in snap_to  that are not in snap_from
      - removed = rows in snap_from that are not in snap_to
    """
    added_sql = f"""
SELECT * FROM "{db}"."{table}" FOR VERSION AS OF {snap_to}
EXCEPT
SELECT * FROM "{db}"."{table}" FOR VERSION AS OF {snap_from}
"""
    removed_sql = f"""
SELECT * FROM "{db}"."{table}" FOR VERSION AS OF {snap_from}
EXCEPT
SELECT * FROM "{db}"."{table}" FOR VERSION AS OF {snap_to}
"""
    added = athena.run(added_sql.strip())
    removed = athena.run(removed_sql.strip())
    return added, removed


# ── Output formatters ──────────────────────────────────────────────────────────

def format_table(added: list[dict], removed: list[dict]) -> str:
    lines = []
    if added:
        lines.append(f"\n{'='*60}")
        lines.append(f"  ADDED ({len(added)} rows)")
        lines.append(f"{'='*60}")
        lines.extend(_dict_rows_to_table(added))
    if removed:
        lines.append(f"\n{'='*60}")
        lines.append(f"  REMOVED ({len(removed)} rows)")
        lines.append(f"{'='*60}")
        lines.extend(_dict_rows_to_table(removed))
    if not added and not removed:
        lines.append("No changes detected between the two snapshots.")
    return "\n".join(lines)


def _dict_rows_to_table(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    sep = "+-" + "-+-".join("-" * widths[c] for c in cols) + "-+"
    header = "| " + " | ".join(c.ljust(widths[c]) for c in cols) + " |"
    lines = [sep, header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols) + " |")
    lines.append(sep)
    return lines


def format_json(added: list[dict], removed: list[dict]) -> str:
    return json.dumps({"added": added, "removed": removed}, indent=2, default=str)


def format_csv(added: list[dict], removed: list[dict]) -> str:
    import csv, io
    buf = io.StringIO()
    all_rows = [{"_change": "ADDED", **r} for r in added] + \
               [{"_change": "REMOVED", **r} for r in removed]
    if not all_rows:
        return "_change\n"
    writer = csv.DictWriter(buf, fieldnames=list(all_rows[0].keys()))
    writer.writeheader()
    writer.writerows(all_rows)
    return buf.getvalue()


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="debugger",
        description="Diff two Iceberg snapshots and produce a row-level changelog via Athena",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Natural-language time range
  python debugger.py --table chicago_buildings.permits \\
      --from "yesterday 5pm" --to "now" \\
      --output-bucket s3://my-athena-results/tmp/

  # Explicit timestamps
  python debugger.py --table analytics.events \\
      --from "2024-03-14 09:00" --to "2024-03-15 09:00" \\
      --format json

  # Show all available snapshots for a table
  python debugger.py --table analytics.events --list-snapshots
""",
    )
    p.add_argument("--table",    required=True,
                   help="Fully qualified Glue table: <database>.<table>")
    p.add_argument("--from",     dest="ts_from", default=None,
                   help="Start timestamp (natural language or ISO 8601)")
    p.add_argument("--to",       dest="ts_to",   default="now",
                   help="End timestamp (default: now)")
    p.add_argument("--workgroup",default="primary",
                   help="Athena workgroup (default: primary)")
    p.add_argument("--output-bucket", default=None,
                   help="S3 output location for Athena results, e.g. s3://bucket/prefix/")
    p.add_argument("--region",   default=None,
                   help="AWS region (default: from environment/profile)")
    p.add_argument("--format",   choices=["table", "json", "csv"], default="table",
                   help="Output format (default: table)")
    p.add_argument("--list-snapshots", action="store_true",
                   help="List all available snapshots for the table and exit")
    return p


def resolve_output_bucket(output_bucket: Optional[str], region: str) -> str:
    if output_bucket:
        return output_bucket.rstrip("/") + "/"
    # Auto-discover the medallion workgroup output location
    client = boto3.client("athena", region_name=region)
    try:
        wg = client.get_work_group(WorkGroup="medallion")
        loc = (wg["WorkGroup"]["Configuration"]
               .get("ResultConfiguration", {})
               .get("OutputLocation"))
        if loc:
            return loc
    except Exception:
        pass
    raise ValueError(
        "Cannot determine Athena output bucket. "
        "Pass --output-bucket s3://your-bucket/prefix/ or use the 'medallion' workgroup."
    )


def resolve_region(region: Optional[str]) -> str:
    if region:
        return region
    session = boto3.session.Session()
    return session.region_name or "us-east-1"


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Parse table reference
    if "." not in args.table:
        parser.error("--table must be <database>.<table>")
    db, table = args.table.split(".", 1)

    region = resolve_region(args.region)
    output_location = resolve_output_bucket(args.output_bucket, region)
    athena = AthenaRunner(args.workgroup, output_location, region)

    # --list-snapshots mode
    if args.list_snapshots:
        sql = f"""
SELECT snapshot_id, committed_at, operation, summary['total-records'] AS total_records
FROM "{db}"."{table}$snapshots"
ORDER BY committed_at DESC
"""
        rows = athena.run(sql.strip())
        if not rows:
            print(f"No snapshots found for {db}.{table}")
            sys.exit(0)
        print(f"\nSnapshots for {db}.{table}:\n")
        print("\n".join(_dict_rows_to_table(rows)))
        sys.exit(0)

    # Require --from for diff mode
    if not args.ts_from:
        parser.error("--from is required for diff mode (or use --list-snapshots)")

    now = datetime.now(timezone.utc)
    try:
        ts_from = parse_natural_time(args.ts_from, now)
        ts_to   = parse_natural_time(args.ts_to,   now)
    except ValueError as e:
        parser.error(str(e))

    if ts_from >= ts_to:
        parser.error(f"--from ({ts_from}) must be before --to ({ts_to})")

    print(f"Table:    {db}.{table}")
    print(f"From:     {ts_from.isoformat()}")
    print(f"To:       {ts_to.isoformat()}")

    # Find snapshots
    print("\nLooking up snapshots...", flush=True)
    snap_from = find_snapshot_at(athena, db, table, ts_from)
    snap_to   = find_snapshot_at(athena, db, table, ts_to)

    if snap_from is None:
        print(f"No snapshot found at or before {ts_from.isoformat()}")
        sys.exit(1)
    if snap_to is None:
        print(f"No snapshot found at or before {ts_to.isoformat()}")
        sys.exit(1)

    print(f"Snapshot (from): {snap_from}")
    print(f"Snapshot (to):   {snap_to}")

    if snap_from == snap_to:
        print("\nBoth timestamps resolve to the same snapshot — no changes.")
        sys.exit(0)

    # Diff
    print("\nDiffing snapshots via Athena...", flush=True)
    added, removed = diff_snapshots(athena, db, table, snap_from, snap_to)

    print(f"\nChangelog: {len(added)} added, {len(removed)} removed\n")

    if args.format == "json":
        print(format_json(added, removed))
    elif args.format == "csv":
        print(format_csv(added, removed))
    else:
        print(format_table(added, removed))


if __name__ == "__main__":
    main()
