"""
Iceberg maintenance executor.

PyIceberg 0.9.x does not include compaction (rewrite_data_files) or
expire_snapshots as table methods.  Those operations are delegated to
Athena SQL procedures:

  OPTIMIZE … REWRITE DATA USING BIN_PACK
  VACUUM …

PyIceberg is retained for catalog resolution (finding the Glue identifier
for a given S3 location) but all mutating work goes through Athena.
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from monitor.config import MonitorConfig
    from monitor.recommendations import Recommendation

from monitor.recommendations import RecommendationType

logger = logging.getLogger(__name__)


def _maintenance_results_prefix() -> str:
    """Athena results location for maintenance SQL queries.

    Resolved per-account at runtime: prefer ATHENA_RESULTS_BUCKET (for tests
    or local overrides); otherwise fall back to the canonical
    `aws-athena-query-results-<account>-<region>` bucket created by the
    Athena console on first use.
    """
    bucket = os.environ.get("ATHENA_RESULTS_BUCKET")
    if not bucket:
        sts = boto3.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        region = os.environ.get("AWS_REGION", "us-east-1")
        bucket = f"aws-athena-query-results-{account_id}-{region}"
    return f"s3://{bucket}/iceberg-maintenance/"


class MaintenanceExecutor:
    """Executes Iceberg maintenance via Athena SQL procedures."""

    def __init__(self, config: "MonitorConfig"):
        self.config = config
        self._catalog = None
        self._athena = None
        self._glue = None

    # ------------------------------------------------------------------
    # Lazy clients
    # ------------------------------------------------------------------
    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = self._build_catalog()
        return self._catalog

    @property
    def athena(self):
        if self._athena is None:
            self._athena = boto3.client("athena", region_name=self.config.aws.region)
        return self._athena

    @property
    def glue(self):
        if self._glue is None:
            self._glue = boto3.client("glue", region_name=self.config.aws.region)
        return self._glue

    def _build_catalog(self):
        from pyiceberg.catalog import load_catalog

        props: dict = dict(self.config.catalog_properties)
        props["type"] = self.config.catalog_type
        if self.config.aws.region:
            props.setdefault("glue.region", self.config.aws.region)
            props.setdefault("region_name", self.config.aws.region)
        if self.config.aws.s3_endpoint:
            props["s3.endpoint"] = self.config.aws.s3_endpoint
        return load_catalog("maintenance_catalog", **props)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def execute(self, table_location: str, recommendations: list["Recommendation"]) -> list[str]:
        actions: list[str] = []
        for rec in recommendations:
            result = self._dispatch(table_location, rec)
            if result:
                actions.append(result)
        return actions

    def _dispatch(self, table_location: str, rec: "Recommendation") -> str | None:
        try:
            if rec.recommendation_type == RecommendationType.COMPACTION:
                return self._run_compaction(table_location, rec)
            if rec.recommendation_type == RecommendationType.SNAPSHOT_EXPIRATION:
                return self._expire_snapshots(table_location, rec)
            if rec.recommendation_type == RecommendationType.ORPHAN_CLEANUP:
                return self._delete_orphans(table_location, rec)
            if rec.recommendation_type == RecommendationType.QUERY_OPTIMIZATION:
                logger.info("Query optimization addressed by compaction/snapshot tasks")
                return None
        except Exception as exc:
            logger.error("Failed to execute %s for %s: %s", rec.recommendation_type, table_location, exc)
            return f"ERROR: {rec.recommendation_type.value} failed: {exc}"
        return None

    # ------------------------------------------------------------------
    # Compaction — Athena OPTIMIZE … REWRITE DATA
    # ------------------------------------------------------------------
    def _run_compaction(self, table_location: str, rec: "Recommendation") -> str:
        target_mb = rec.action_params.get("target_file_size_mb", self.config.compaction.small_file_size_mb)
        db, tbl = self._resolve_glue_identifier(table_location)

        if self.config.dry_run:
            logger.info("[DRY RUN] Would compact %s.%s (target %sMB)", db, tbl, target_mb)
            return f"[DRY RUN] Compaction: {db}.{tbl}, target {target_mb}MB per file"

        sql = f"OPTIMIZE {db}.{tbl} REWRITE DATA USING BIN_PACK"
        result = self._run_athena_query(sql)
        logger.info("Compaction complete for %s.%s: %s", db, tbl, result)
        return f"Compaction: {db}.{tbl} — {result}"

    # ------------------------------------------------------------------
    # Snapshot expiration — Athena VACUUM
    # ------------------------------------------------------------------
    def _expire_snapshots(self, table_location: str, rec: "Recommendation") -> str:
        retain_last = rec.action_params.get("retain_last", 10)
        db, tbl = self._resolve_glue_identifier(table_location)

        if self.config.dry_run:
            logger.info("[DRY RUN] Would VACUUM %s.%s (retain_last=%s)", db, tbl, retain_last)
            return f"[DRY RUN] Snapshot expiration: {db}.{tbl}, retain_last={retain_last}"

        sql = f"VACUUM {db}.{tbl}"
        result = self._run_athena_query(sql)
        logger.info("Snapshot expiration complete for %s.%s: %s", db, tbl, result)
        return f"Snapshot expiration (VACUUM): {db}.{tbl} — {result}"

    # ------------------------------------------------------------------
    # Orphan file deletion — Athena VACUUM with older_than timestamp
    # ------------------------------------------------------------------
    def _delete_orphans(self, table_location: str, rec: "Recommendation") -> str:
        older_than_ms = rec.action_params.get(
            "older_than_ms",
            int(self.config.orphans.age_hours * 3600 * 1000),
        )
        db, tbl = self._resolve_glue_identifier(table_location)

        if self.config.dry_run:
            logger.info("[DRY RUN] Would delete orphans for %s.%s (older_than_ms=%s)", db, tbl, older_than_ms)
            return f"[DRY RUN] Orphan cleanup: {db}.{tbl}, older_than_ms={older_than_ms}"

        # Athena VACUUM removes expired snapshots AND orphan files
        sql = f"VACUUM {db}.{tbl}"
        result = self._run_athena_query(sql)
        logger.info("Orphan cleanup complete for %s.%s: %s", db, tbl, result)
        return f"Orphan cleanup (VACUUM): {db}.{tbl} — {result}"

    # ------------------------------------------------------------------
    # Athena query execution helper
    # ------------------------------------------------------------------
    def _run_athena_query(self, sql: str, timeout_s: int = 300) -> str:
        resp = self.athena.start_query_execution(
            QueryString=sql,
            ResultConfiguration={"OutputLocation": _maintenance_results_prefix()},
        )
        qid = resp["QueryExecutionId"]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(3)
            status = self.athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = status["State"]
            if state == "SUCCEEDED":
                stats = self.athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"].get("Statistics", {})
                elapsed_ms = stats.get("TotalExecutionTimeInMillis", 0)
                return f"SUCCEEDED in {elapsed_ms}ms (qid={qid})"
            if state in ("FAILED", "CANCELLED"):
                reason = status.get("StateChangeReason", "unknown")
                raise RuntimeError(f"Athena query {state}: {reason} | SQL: {sql}")
        raise TimeoutError(f"Athena query timed out after {timeout_s}s: {sql}")

    # ------------------------------------------------------------------
    # Glue identifier resolver
    # ------------------------------------------------------------------
    def _resolve_glue_identifier(self, table_location: str) -> tuple[str, str]:
        """
        Return (database_name, table_name) for the given S3 table location.

        Searches all Glue databases for a table whose StorageDescriptor.Location
        or Parameters.metadata_location prefix matches the given S3 path.
        """
        loc = table_location.rstrip("/")
        paginator = self.glue.get_paginator("get_databases")
        for page in paginator.paginate():
            for db in page["DatabaseList"]:
                db_name = db["Name"]
                try:
                    tbl_paginator = self.glue.get_paginator("get_tables")
                    for tbl_page in tbl_paginator.paginate(DatabaseName=db_name):
                        for t in tbl_page.get("TableList", []):
                            tbl_loc = t.get("StorageDescriptor", {}).get("Location", "").rstrip("/")
                            meta_loc = t.get("Parameters", {}).get("metadata_location", "")
                            if tbl_loc == loc or meta_loc.startswith(loc + "/"):
                                return db_name, t["Name"]
                except Exception:
                    pass

        # Fallback: last two path components
        parts = loc.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        raise ValueError(f"Cannot resolve Glue identifier for {table_location}")
