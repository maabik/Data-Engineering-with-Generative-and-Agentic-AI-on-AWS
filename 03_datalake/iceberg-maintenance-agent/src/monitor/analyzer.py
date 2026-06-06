from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from monitor.config import MonitorConfig

from monitor.metrics import (
    FileMetrics,
    OrphanMetrics,
    QueryLatencyMetrics,
    SnapshotMetrics,
    TableMetrics,
)

logger = logging.getLogger(__name__)

_MB = 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TableAnalyzer:
    def __init__(self, config: "MonitorConfig"):
        self.config = config
        self._s3 = None
        self._athena = None
        self._cloudwatch = None

    # ------------------------------------------------------------------
    # Lazy boto3 clients
    # ------------------------------------------------------------------
    @property
    def s3(self):
        if self._s3 is None:
            kwargs = {"region_name": self.config.aws.region}
            if self.config.aws.s3_endpoint:
                kwargs["endpoint_url"] = self.config.aws.s3_endpoint
            self._s3 = boto3.client("s3", **kwargs)
        return self._s3

    @property
    def athena(self):
        if self._athena is None:
            self._athena = boto3.client("athena", region_name=self.config.aws.region)
        return self._athena

    @property
    def cloudwatch(self):
        if self._cloudwatch is None:
            self._cloudwatch = boto3.client("cloudwatch", region_name=self.config.aws.region)
        return self._cloudwatch

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def analyze_table(self, table_location: str) -> TableMetrics:
        logger.info("Analyzing table: %s", table_location)
        bucket, prefix = self._parse_s3_path(table_location)

        file_metrics = self._collect_file_metrics(bucket, prefix)
        snapshot_metrics = self._collect_snapshot_metrics(bucket, prefix)
        orphan_metrics = self._collect_orphan_metrics(bucket, prefix, snapshot_metrics)
        query_latency = self._collect_query_latency(table_location)

        # Use current snapshot id from metadata if available
        snapshot_id = self._get_current_snapshot_id(bucket, prefix)

        return TableMetrics(
            table_location=table_location,
            snapshot_id=snapshot_id,
            analysis_timestamp=_utcnow().isoformat(),
            file_metrics=file_metrics,
            snapshot_metrics=snapshot_metrics,
            orphan_metrics=orphan_metrics,
            query_latency=query_latency,
        )

    # ------------------------------------------------------------------
    # File metrics
    # ------------------------------------------------------------------
    def _collect_file_metrics(self, bucket: str, prefix: str) -> FileMetrics:
        data_prefix = prefix.rstrip("/") + "/data/"
        paginator = self.s3.get_paginator("list_objects_v2")

        sizes: list[float] = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=data_prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".parquet"):
                        sizes.append(obj["Size"] / _MB)
        except Exception as exc:
            logger.warning("Could not list data files for %s/%s: %s", bucket, data_prefix, exc)
            return FileMetrics()

        if not sizes:
            return FileMetrics()

        small_threshold = self.config.compaction.small_file_size_mb
        small_files = sum(1 for s in sizes if s < small_threshold)
        total_size = sum(sizes)
        avg_size = total_size / len(sizes)
        sorted_sizes = sorted(sizes)
        mid = len(sorted_sizes) // 2
        median_size = sorted_sizes[mid]

        dist: dict[str, int] = {
            "0-50MB": 0, "50-100MB": 0, "100-200MB": 0, "200-500MB": 0, "500MB+": 0
        }
        for s in sizes:
            if s < 50:
                dist["0-50MB"] += 1
            elif s < 100:
                dist["50-100MB"] += 1
            elif s < 200:
                dist["100-200MB"] += 1
            elif s < 500:
                dist["200-500MB"] += 1
            else:
                dist["500MB+"] += 1

        return FileMetrics(
            total_files=len(sizes),
            small_files=small_files,
            average_file_size_mb=round(avg_size, 2),
            median_file_size_mb=round(median_size, 2),
            total_size_mb=round(total_size, 2),
            file_size_distribution=dist,
        )

    # ------------------------------------------------------------------
    # Snapshot metrics — parse Iceberg metadata JSON
    # ------------------------------------------------------------------
    def _collect_snapshot_metrics(self, bucket: str, prefix: str) -> SnapshotMetrics:
        metadata = self._load_iceberg_metadata(bucket, prefix)
        if not metadata:
            return SnapshotMetrics()

        snapshots = metadata.get("snapshots", [])
        if not snapshots:
            return SnapshotMetrics()

        now_ms = int(_utcnow().timestamp() * 1000)
        retention_ms = int(self.config.snapshots.retention_hours * 3600 * 1000)

        timestamps = [s.get("timestamp-ms", now_ms) for s in snapshots]
        oldest_ms = min(timestamps)
        newest_ms = max(timestamps)

        oldest_age_h = (now_ms - oldest_ms) / 3_600_000
        newest_age_h = (now_ms - newest_ms) / 3_600_000

        to_expire = sum(1 for ts in timestamps if (now_ms - ts) > retention_ms)
        meta_size_per_snap_mb = 0.5  # rough estimate
        reduction_mb = to_expire * meta_size_per_snap_mb

        return SnapshotMetrics(
            total_snapshots=len(snapshots),
            oldest_snapshot_age_hours=round(oldest_age_h, 1),
            newest_snapshot_age_hours=round(newest_age_h, 1),
            snapshots_to_expire=to_expire,
            estimated_metadata_reduction_mb=round(reduction_mb, 2),
        )

    # ------------------------------------------------------------------
    # Orphan metrics — files not referenced in any snapshot manifest
    # ------------------------------------------------------------------
    def _collect_orphan_metrics(
        self, bucket: str, prefix: str, snapshot_metrics: SnapshotMetrics
    ) -> OrphanMetrics:
        metadata = self._load_iceberg_metadata(bucket, prefix)
        if not metadata:
            return OrphanMetrics()

        referenced = self._collect_referenced_files(bucket, prefix, metadata)
        data_prefix = prefix.rstrip("/") + "/data/"
        paginator = self.s3.get_paginator("list_objects_v2")

        now = _utcnow()
        age_cutoff = now - timedelta(hours=self.config.orphans.age_hours)

        orphan_paths: list[str] = []
        orphan_size_mb = 0.0

        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=data_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(".parquet"):
                        continue
                    s3_path = f"s3://{bucket}/{key}"
                    last_modified = obj.get("LastModified", now)
                    if last_modified.tzinfo is None:
                        last_modified = last_modified.replace(tzinfo=timezone.utc)
                    if s3_path not in referenced and last_modified < age_cutoff:
                        orphan_paths.append(s3_path)
                        orphan_size_mb += obj["Size"] / _MB
        except Exception as exc:
            logger.warning("Could not detect orphan files: %s", exc)
            return OrphanMetrics()

        return OrphanMetrics(
            orphan_file_count=len(orphan_paths),
            orphan_total_size_mb=round(orphan_size_mb, 2),
            orphan_file_paths=orphan_paths[:200],  # cap to avoid huge payloads
        )

    def _collect_referenced_files(self, bucket: str, prefix: str, metadata: dict) -> set[str]:
        """Walk all snapshot manifests and collect referenced data file paths."""
        referenced: set[str] = set()
        for snapshot in metadata.get("snapshots", []):
            manifest_list = snapshot.get("manifest-list", "")
            if not manifest_list:
                continue
            try:
                manifests = self._read_manifest_list(manifest_list)
                for manifest_path in manifests:
                    files = self._read_manifest(manifest_path)
                    referenced.update(files)
            except Exception as exc:
                logger.debug("Could not read manifest %s: %s", manifest_list, exc)
        return referenced

    # ------------------------------------------------------------------
    # Query latency from Athena query execution history
    # ------------------------------------------------------------------
    def _collect_query_latency(self, table_location: str) -> QueryLatencyMetrics:
        table_name = table_location.rstrip("/").split("/")[-1]
        try:
            return self._query_latency_from_athena(table_name)
        except Exception as exc:
            logger.debug("Could not collect query latency for %s: %s", table_name, exc)
            return QueryLatencyMetrics()

    def _query_latency_from_athena(self, table_name: str) -> QueryLatencyMetrics:
        paginator = self.athena.get_paginator("list_query_executions")
        execution_ids: list[str] = []
        for page in paginator.paginate():
            execution_ids.extend(page.get("QueryExecutionIds", []))
            if len(execution_ids) >= 500:
                break

        if not execution_ids:
            return QueryLatencyMetrics()

        latencies_ms: list[float] = []
        now = _utcnow()
        cutoff_7d = now - timedelta(days=7)
        cutoff_1d = now - timedelta(days=1)

        # Batch-fetch in chunks of 50 (Athena API limit)
        for i in range(0, min(len(execution_ids), 500), 50):
            chunk = execution_ids[i : i + 50]
            try:
                resp = self.athena.batch_get_query_execution(QueryExecutionIds=chunk)
            except Exception:
                continue
            for qe in resp.get("QueryExecutions", []):
                query_str = qe.get("Query", "").lower()
                if table_name.lower() not in query_str:
                    continue
                status = qe.get("Status", {})
                if status.get("State") != "SUCCEEDED":
                    continue
                stats = qe.get("Statistics", {})
                exec_ms = stats.get("TotalExecutionTimeInMillis")
                if exec_ms is None:
                    continue
                sub_ts = status.get("SubmissionDateTime")
                if sub_ts and sub_ts.tzinfo is None:
                    sub_ts = sub_ts.replace(tzinfo=timezone.utc)
                if sub_ts and sub_ts >= cutoff_7d:
                    latencies_ms.append(float(exec_ms))

        if not latencies_ms:
            return QueryLatencyMetrics()

        latencies_ms.sort()
        n = len(latencies_ms)

        def percentile(p: float) -> float:
            idx = int(n * p / 100)
            return latencies_ms[min(idx, n - 1)]

        p50 = percentile(50)
        p95 = percentile(95)
        p99 = percentile(99)
        avg = sum(latencies_ms) / n

        # Baseline: first 6 days vs last 1 day
        recent_ids: list[str] = []
        for i in range(0, min(len(execution_ids), 500), 50):
            chunk = execution_ids[i : i + 50]
            try:
                resp = self.athena.batch_get_query_execution(QueryExecutionIds=chunk)
            except Exception:
                continue
            for qe in resp.get("QueryExecutions", []):
                query_str = qe.get("Query", "").lower()
                if table_name.lower() not in query_str:
                    continue
                status = qe.get("Status", {})
                if status.get("State") != "SUCCEEDED":
                    continue
                stats = qe.get("Statistics", {})
                exec_ms = stats.get("TotalExecutionTimeInMillis")
                if exec_ms is None:
                    continue
                sub_ts = status.get("SubmissionDateTime")
                if sub_ts and sub_ts.tzinfo is None:
                    sub_ts = sub_ts.replace(tzinfo=timezone.utc)
                if sub_ts and cutoff_7d <= sub_ts < cutoff_1d:
                    recent_ids.append(float(exec_ms))

        baseline_p95 = p95
        if recent_ids:
            recent_ids.sort()
            nr = len(recent_ids)
            baseline_p95 = recent_ids[min(int(nr * 0.95), nr - 1)]

        degradation_pct = 0.0
        if baseline_p95 > 0:
            degradation_pct = (p95 - baseline_p95) / baseline_p95 * 100

        return QueryLatencyMetrics(
            sample_count=n,
            p50_ms=round(p50, 1),
            p95_ms=round(p95, 1),
            p99_ms=round(p99, 1),
            avg_ms=round(avg, 1),
            baseline_p95_ms=round(baseline_p95, 1),
            degradation_pct=round(degradation_pct, 1),
            query_engine="athena",
        )

    # ------------------------------------------------------------------
    # Iceberg metadata helpers
    # ------------------------------------------------------------------
    def _load_iceberg_metadata(self, bucket: str, prefix: str) -> dict | None:
        import json

        meta_prefix = prefix.rstrip("/") + "/metadata/"
        paginator = self.s3.get_paginator("list_objects_v2")
        metadata_files = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=meta_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith(".json") and "metadata" in key:
                        metadata_files.append((key, obj.get("LastModified")))
        except Exception as exc:
            logger.warning("Could not list metadata for %s/%s: %s", bucket, meta_prefix, exc)
            return None

        if not metadata_files:
            return None

        # Latest metadata file
        metadata_files.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        latest_key = metadata_files[0][0]
        try:
            resp = self.s3.get_object(Bucket=bucket, Key=latest_key)
            return json.loads(resp["Body"].read())
        except Exception as exc:
            logger.warning("Could not read metadata file %s: %s", latest_key, exc)
            return None

    def _get_current_snapshot_id(self, bucket: str, prefix: str) -> str:
        metadata = self._load_iceberg_metadata(bucket, prefix)
        if not metadata:
            return ""
        return str(metadata.get("current-snapshot-id", ""))

    def _read_manifest_list(self, manifest_list_path: str) -> list[str]:
        """Return list of manifest file paths from a manifest list (Avro)."""
        import pyarrow.dataset as ds
        bucket, key = self._parse_s3_path(manifest_list_path)
        try:
            resp = self.s3.get_object(Bucket=bucket, Key=key)
            data = resp["Body"].read()
            import io
            import pyarrow as pa
            reader = pa.ipc.open_file(io.BytesIO(data))
            table = reader.read_all()
            return table.column("manifest_path").to_pylist()
        except Exception:
            return []

    def _read_manifest(self, manifest_path: str) -> list[str]:
        """Return list of data file paths from a manifest file (Avro)."""
        bucket, key = self._parse_s3_path(manifest_path)
        try:
            resp = self.s3.get_object(Bucket=bucket, Key=key)
            data = resp["Body"].read()
            import io
            import pyarrow as pa
            reader = pa.ipc.open_file(io.BytesIO(data))
            table = reader.read_all()
            return table.column("file_path").to_pylist()
        except Exception:
            return []

    @staticmethod
    def _parse_s3_path(path: str) -> tuple[str, str]:
        path = path.removeprefix("s3://")
        bucket, _, prefix = path.partition("/")
        return bucket, prefix
