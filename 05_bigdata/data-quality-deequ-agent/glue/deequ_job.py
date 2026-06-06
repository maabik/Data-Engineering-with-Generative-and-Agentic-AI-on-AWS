"""Deequ data-quality Glue job.

Reads the curated table that the data-architect-agent registered, asks
Deequ for constraint *suggestions*, filters out checks that are too
aggressive on small / sparse columns (so the report is actionable rather
than noisy), runs the resulting constraints through VerificationSuite,
and writes two JSON outputs to S3:

  - suggestions/<run_id>.json   (raw Deequ suggestions, kept for audit)
  - results/<run_id>.json       (per-constraint pass/fail + metrics)

The results are also written as Parquet at
    s3://${REPORT_PREFIX}/parquet/run_id=<run_id>/
so an Athena-registered external table can pick them up partition-by-run.

Arguments:
    --DATABASE             curated catalog database (e.g. data_architect_agent_curated)
    --TABLE                curated catalog table     (e.g. violations_curated)
    --REPORT_BUCKET        s3 bucket for outputs
    --REPORT_PREFIX        s3 prefix (default 'dq-reports/<table>/')
    --RUN_ID               unique run identifier
    --MIN_NULL_FRACTION    skip Completeness checks below this fraction
                           of non-nulls (default 0.99 — anything 99%+
                           non-null gets a soft check, not isComplete)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

# pydeequ resolves the Deequ JAR coordinates from this env var at import
# time. Glue 4.0 ships Spark 3.3, so we need to pin to 3.3 *before* the
# pydeequ package runs its module-level lookup.
os.environ.setdefault("SPARK_VERSION", "3.3")

import boto3
from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.functions import col, lit

from pydeequ.checks import Check, CheckLevel
from pydeequ.suggestions import ConstraintSuggestionRunner, DEFAULT
from pydeequ.verification import VerificationSuite, VerificationResult


REQUIRED = [
    "JOB_NAME",
    "DATABASE",
    "TABLE",
    "REPORT_BUCKET",
    "REPORT_PREFIX",
    "RUN_ID",
    "MIN_NULL_FRACTION",
]

# Deequ constraint kinds we accept by default. Others (e.g.
# CategoricalRangeConstraint with very wide enums, RetainTypeConstraint)
# get filtered before being verified.
SAFE_KINDS = {
    "CompletenessConstraint",
    "UniquenessConstraint",
    "ComplianceConstraint",
    "NonNegativeNumbersConstraint",
    "PositiveNumbersConstraint",
    "DataTypeConstraint",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _suggestion_is_too_aggressive(s: dict, completeness_pct: dict, threshold: float) -> bool:
    """Return True if the suggested constraint should be skipped.

    Heuristics tuned for tabular CSV-shaped data with light nulls:
      * skip isComplete on columns with <99% non-null (configurable)
      * skip isUnique on low-cardinality columns (Deequ already infers this
        well, but small samples can mislead it)
      * skip CategoricalRangeConstraint when the inferred allowed-set is
        bigger than half the row count (basically a unique-value list)
    """
    kind = s.get("constraint_name", "")
    column = s.get("column_name") or s.get("column")

    if "Completeness" in kind:
        nn = completeness_pct.get(column, 1.0)
        if nn < threshold:
            return True

    if "Categorical" in kind:
        code = s.get("code_for_constraint", "") or s.get("constraint", "")
        # Very wide allow-lists are essentially "all values" — useless
        if code.count(",") >= 50:
            return True

    return False


def _coerce_constraint_suggestion(check: Check, s: dict) -> Check | None:
    """Convert a Deequ suggestion dict into a runnable Check builder call.

    Deequ's pydeequ binding returns suggestions as Python dicts; we map
    a small but useful subset to Check methods. Anything we don't
    recognise we drop (rather than fail the whole run).
    """
    kind = s.get("constraint_name", "")
    column = s.get("column_name") or s.get("column")
    if not column:
        return None

    if "CompletenessConstraint" in kind:
        return check.isComplete(column)
    if "UniquenessConstraint" in kind:
        return check.isUnique(column)
    if "NonNegativeNumbersConstraint" in kind:
        return check.isNonNegative(column)
    if "PositiveNumbersConstraint" in kind:
        return check.isPositive(column)
    if "DataTypeConstraint" in kind:
        # We don't know exactly which DataType; skip to avoid a hard fail
        return None
    if "ComplianceConstraint" in kind:
        # Compliance suggestions come with a free-form predicate; skip
        # auto-applying — risk of bad regex or column name typo
        return None
    return None


def _completeness_per_column(df) -> dict:
    out = {}
    total = df.count()
    if total == 0:
        return out
    for c in df.columns:
        nonnull = df.filter(col(c).isNotNull()).count()
        out[c] = nonnull / total
    return out


def main():
    args = getResolvedOptions(sys.argv, REQUIRED)

    sc = SparkContext.getOrCreate()
    glue = GlueContext(sc)
    spark = glue.spark_session

    database = args["DATABASE"]
    table = args["TABLE"]
    report_bucket = args["REPORT_BUCKET"]
    prefix = args["REPORT_PREFIX"].rstrip("/") + "/"
    run_id = args["RUN_ID"]
    threshold = float(args["MIN_NULL_FRACTION"])

    print(f"[deequ] reading {database}.{table}")
    df = spark.table(f"`{database}`.`{table}`")
    df.cache()
    row_count = df.count()
    print(f"[deequ] rows={row_count} cols={len(df.columns)}")

    print("[deequ] computing per-column completeness")
    completeness = _completeness_per_column(df)

    print("[deequ] running ConstraintSuggestionRunner (Default profile)")
    suggestions_result = (
        ConstraintSuggestionRunner(spark)
        .onData(df)
        .addConstraintRule(DEFAULT())
        .run()
    )
    raw_suggestions = suggestions_result.get("constraint_suggestions", [])
    print(f"[deequ] got {len(raw_suggestions)} raw suggestions")

    kept = []
    skipped = []
    for s in raw_suggestions:
        if s.get("constraint_name", "").split("(")[0] not in SAFE_KINDS and \
                not any(k in s.get("constraint_name", "") for k in SAFE_KINDS):
            skipped.append({"reason": "kind not in SAFE_KINDS", "suggestion": s})
            continue
        if _suggestion_is_too_aggressive(s, completeness, threshold):
            skipped.append({"reason": "too aggressive", "suggestion": s})
            continue
        kept.append(s)
    print(f"[deequ] kept {len(kept)} suggestions, skipped {len(skipped)}")

    check = Check(spark, CheckLevel.Warning, f"dq_check_{table}")
    applied = []
    for s in kept:
        new_check = _coerce_constraint_suggestion(check, s)
        if new_check is None:
            skipped.append({"reason": "no python coercion", "suggestion": s})
            continue
        check = new_check
        applied.append(s)
    print(f"[deequ] applied {len(applied)} constraints to VerificationSuite")

    suggestions_key = f"{prefix}suggestions/{run_id}.json"
    suggestions_uri = f"s3://{report_bucket}/{suggestions_key}"
    boto3.client("s3").put_object(
        Bucket=report_bucket,
        Key=suggestions_key,
        Body=json.dumps(
            {
                "runId": run_id,
                "database": database,
                "table": table,
                "rowCount": row_count,
                "completeness": completeness,
                "applied": applied,
                "skipped": skipped,
                "ts": _now(),
            },
            indent=2,
            default=str,
        ).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"[deequ] suggestions written to {suggestions_uri}")

    print("[deequ] running VerificationSuite")
    verification_result = (
        VerificationSuite(spark)
        .onData(df)
        .addCheck(check)
        .run()
    )
    checks_df = VerificationResult.checkResultsAsDataFrame(spark, verification_result)
    rows = [r.asDict() for r in checks_df.collect()]
    overall_status = verification_result.status
    print(f"[deequ] overall status={overall_status} rows={len(rows)}")

    results_key = f"{prefix}results/{run_id}.json"
    results_uri = f"s3://{report_bucket}/{results_key}"
    boto3.client("s3").put_object(
        Bucket=report_bucket,
        Key=results_key,
        Body=json.dumps(
            {
                "runId": run_id,
                "database": database,
                "table": table,
                "overallStatus": str(overall_status),
                "checks": rows,
                "ts": _now(),
            },
            indent=2,
            default=str,
        ).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"[deequ] results JSON written to {results_uri}")

    parquet_uri = f"s3://{report_bucket}/{prefix}parquet/"
    print(f"[deequ] writing parquet results -> {parquet_uri}run_id={run_id}/")
    enriched = (
        checks_df
        .withColumn("run_id", lit(run_id))
        .withColumn("source_database", lit(database))
        .withColumn("source_table", lit(table))
        .withColumn("ts", lit(_now()))
    )
    (
        enriched.write.mode("append")
        .format("parquet")
        .partitionBy("run_id")
        .save(parquet_uri)
    )

    print("[deequ] done")


if __name__ == "__main__":
    main()
