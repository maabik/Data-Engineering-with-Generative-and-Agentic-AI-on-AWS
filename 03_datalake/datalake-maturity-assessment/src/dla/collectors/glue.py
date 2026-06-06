"""Glue Data Catalog + Glue Jobs + Crawlers + Triggers collector."""
from __future__ import annotations

from typing import Any

from ._util import make_client, paginate, safe_call

ICEBERG_PARAM_KEYS = ("table_type", "metadata_location")
DELTA_INPUT_FORMAT = "org.apache.hadoop.hive.ql.io.SymlinkTextInputFormat"
DEEQU_PATTERNS = ("deequ", "VerificationSuite", "Constraint")
GE_PATTERNS = ("great_expectations", "ge.from_pandas", "great-expectations")
LINEAGE_PATTERNS = ("openlineage", "OpenLineage", "marquez")


def _classify_table(t: dict[str, Any]) -> str:
    params = {k.lower(): v for k, v in (t.get("Parameters") or {}).items()}
    if any(params.get(k, "").lower() in ("iceberg",) for k in ICEBERG_PARAM_KEYS):
        return "iceberg"
    if "metadata_location" in params:
        return "iceberg"
    sd = t.get("StorageDescriptor", {}) or {}
    fmt = (sd.get("InputFormat") or "").lower()
    out = (sd.get("OutputFormat") or "").lower()
    serde = (sd.get("SerdeInfo", {}) or {}).get("SerializationLibrary", "").lower()
    if "delta" in fmt or "delta" in serde:
        return "delta"
    if "hudi" in fmt or "hudi" in serde:
        return "hudi"
    if "parquet" in fmt or "parquet" in serde:
        return "parquet"
    if "orc" in fmt or "orc" in serde:
        return "orc"
    if "avro" in fmt or "avro" in serde:
        return "avro"
    if "json" in fmt or "json" in serde:
        return "json"
    if "csv" in fmt or "lazysimple" in serde:
        return "csv"
    return "other"


def _has_descriptions(t: dict[str, Any]) -> bool:
    cols = (t.get("StorageDescriptor", {}) or {}).get("Columns", []) or []
    if not cols:
        return False
    described = sum(1 for c in cols if c.get("Comment"))
    return described >= max(1, int(0.8 * len(cols)))


def collect_glue(region: str, profile: str | None = None) -> dict[str, Any]:
    glue = make_client("glue", region, profile)
    databases = paginate(glue, "get_databases", "DatabaseList")
    tables: list[dict[str, Any]] = []
    for db in databases:
        db_tables = paginate(glue, "get_tables", "TableList", DatabaseName=db["Name"])
        for t in db_tables:
            t["_DatabaseName"] = db["Name"]
            tables.append(t)

    crawlers = paginate(glue, "get_crawlers", "Crawlers")
    jobs = paginate(glue, "get_jobs", "Jobs")
    triggers = paginate(glue, "get_triggers", "Triggers")

    # Look at job scripts for quality / lineage signatures
    job_signals = {"deequ": 0, "great_expectations": 0, "openlineage": 0, "lineage_config_enabled": 0}
    for j in jobs:
        cmd = j.get("Command", {}) or {}
        script_loc = cmd.get("ScriptLocation", "")
        # We don't fetch the script here (would require S3 GetObject and could be large).
        # Heuristic: check job name + default arguments for the pattern.
        haystack = " ".join([
            j.get("Name", ""),
            script_loc,
            *[f"{k}={v}" for k, v in (j.get("DefaultArguments") or {}).items()],
        ])
        hl = haystack.lower()
        if any(p.lower() in hl for p in DEEQU_PATTERNS):
            job_signals["deequ"] += 1
        if any(p.lower() in hl for p in GE_PATTERNS):
            job_signals["great_expectations"] += 1
        if any(p.lower() in hl for p in LINEAGE_PATTERNS):
            job_signals["openlineage"] += 1

    # Crawler lineage configuration
    for c in crawlers:
        cfg = c.get("LineageConfiguration", {}) or {}
        if cfg.get("CrawlerLineageSettings") == "ENABLE":
            job_signals["lineage_config_enabled"] += 1

    fmt_counts: dict[str, int] = {}
    iceberg_tables = []
    described = 0
    partitioned = 0
    for t in tables:
        fmt = _classify_table(t)
        fmt_counts[fmt] = fmt_counts.get(fmt, 0) + 1
        if fmt == "iceberg":
            iceberg_tables.append(f"{t['_DatabaseName']}.{t['Name']}")
        if _has_descriptions(t):
            described += 1
        if t.get("PartitionKeys"):
            partitioned += 1

    return {
        "databases": [d["Name"] for d in databases],
        "table_count": len(tables),
        "table_format_counts": fmt_counts,
        "iceberg_table_count": fmt_counts.get("iceberg", 0),
        "delta_table_count": fmt_counts.get("delta", 0),
        "hudi_table_count": fmt_counts.get("hudi", 0),
        "parquet_table_count": fmt_counts.get("parquet", 0),
        "described_tables": described,
        "partitioned_tables": partitioned,
        "iceberg_table_names_sample": iceberg_tables[:10],
        "crawler_count": len(crawlers),
        "job_count": len(jobs),
        "trigger_count": len(triggers),
        "scheduled_trigger_count": sum(1 for t in triggers if t.get("Type") == "SCHEDULED"),
        "job_signals": job_signals,
    }
