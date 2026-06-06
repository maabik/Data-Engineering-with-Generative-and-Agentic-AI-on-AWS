"""Storage & Table Format dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore  # avoid circular at module init

    glue = snapshot.get("glue", {}) or {}
    s3t = snapshot.get("s3tables", {}) or {}
    s3 = snapshot.get("s3", {}) or {}
    athena = snapshot.get("athena", {}) or {}

    iceberg_n = glue.get("iceberg_table_count", 0)
    delta_n = glue.get("delta_table_count", 0)
    hudi_n = glue.get("hudi_table_count", 0)
    parquet_n = glue.get("parquet_table_count", 0)
    total = max(1, glue.get("table_count", 0))
    open_format_share = (iceberg_n + delta_n + hudi_n) / total
    columnar_share = (iceberg_n + delta_n + hudi_n + parquet_n) / total

    s3tables_present = s3t.get("table_count", 0) > 0

    # Look across inspected lake buckets for partition signals + columnar share
    inspected = s3.get("inspected", []) or []
    parquet_objects = sum(b.get("sample_formats", {}).get("parquet", 0) + b.get("sample_formats", {}).get("orc", 0) for b in inspected)
    total_objects = sum(b.get("sample_object_count", 0) for b in inspected) or 1
    s3_columnar_share = parquet_objects / total_objects
    partition_signals = sum(b.get("hive_partition_signals", 0) for b in inspected)
    delta_log_signals = sum(b.get("sample_formats", {}).get("delta_log", 0) for b in inspected)
    iceberg_meta_signals = sum(b.get("sample_formats", {}).get("iceberg_metadata", 0) for b in inspected)

    # Materialized views in Athena query history
    mv_signals = athena.get("materialized_view_signals", 0)

    evidence = [
        f"Glue tables: {glue.get('table_count', 0)} (iceberg={iceberg_n}, delta={delta_n}, hudi={hudi_n}, parquet={parquet_n})",
        f"S3 Tables: {'detected ' + str(s3t.get('table_count', 0)) + ' tables' if s3tables_present else 'not used'}",
        f"Sampled lake S3 objects: {total_objects} (columnar share {s3_columnar_share:.0%}, partition signals {partition_signals})",
    ]
    if iceberg_meta_signals or delta_log_signals:
        evidence.append(f"Open-table format signals on disk: iceberg_metadata={iceberg_meta_signals}, delta_log={delta_log_signals}")
    if mv_signals:
        evidence.append(f"Athena materialized view CREATE/REFRESH events seen: {mv_signals}")

    # Level decision
    level = 1
    if columnar_share >= 0.5 or s3_columnar_share >= 0.5 or glue.get("partitioned_tables", 0) > 0:
        level = 2
    if open_format_share >= 0.1 or iceberg_meta_signals > 0 or delta_log_signals > 0:
        level = 3
    if s3tables_present or (iceberg_n >= 5 and glue.get("scheduled_trigger_count", 0) >= 1):
        level = 4
    if level >= 4 and mv_signals > 0:
        level = 5

    if level == 1:
        top_gap = "Adopt a columnar format (Parquet) and register tables in Glue Data Catalog."
    elif level == 2:
        top_gap = "Migrate at least one core table to Apache Iceberg or S3 Tables."
    elif level == 3:
        top_gap = "Schedule maintenance (OPTIMIZE/VACUUM/EXPIRE_SNAPSHOTS) or move to S3 Tables managed buckets."
    elif level == 4:
        top_gap = "Tune sort/cluster keys and add materialized views for hot query patterns."
    else:
        top_gap = "—"

    return DimensionScore(name="storage_format", level=level, evidence=evidence, top_gap=top_gap)
