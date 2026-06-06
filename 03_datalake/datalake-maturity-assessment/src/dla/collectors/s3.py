"""S3 collector.

Strategy: list all buckets, but only deeply inspect those that look like data-lake
buckets (`raw/`, `bronze/`, `silver/`, `gold/`, or `warehouse/` prefixes, or
`*lake*`/`*warehouse*` in the name). For each, sample object types and check
encryption, lifecycle, public-access-block, tags, and replication.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ._util import make_client, safe_call

LAKE_NAME_HINTS = ("lake", "warehouse", "iceberg", "delta", "hudi", "raw", "bronze", "silver", "gold", "medallion", "lakehouse", "datazone")
LAKE_PREFIX_HINTS = ("raw/", "bronze/", "silver/", "gold/", "warehouse/", "lakehouse/", "iceberg/", "delta/")
SAMPLE_KEYS_PER_BUCKET = 200
MAX_DEEP_INSPECT = 50  # cap deep inspection to keep scan time bounded


def _looks_like_lake(name: str) -> bool:
    n = name.lower()
    return any(h in n for h in LAKE_NAME_HINTS)


def _classify_keys(keys: list[str]) -> dict[str, int]:
    fmt = Counter()
    for k in keys:
        kl = k.lower()
        if kl.endswith(".parquet"):
            fmt["parquet"] += 1
        elif kl.endswith(".orc"):
            fmt["orc"] += 1
        elif kl.endswith(".avro"):
            fmt["avro"] += 1
        elif kl.endswith(".json") or kl.endswith(".json.gz"):
            fmt["json"] += 1
        elif kl.endswith(".csv") or kl.endswith(".csv.gz") or kl.endswith(".tsv"):
            fmt["csv"] += 1
        elif "/_delta_log/" in kl:
            fmt["delta_log"] += 1
        elif "/metadata/" in kl and (kl.endswith(".metadata.json") or kl.endswith(".avro")):
            fmt["iceberg_metadata"] += 1
        else:
            fmt["other"] += 1
    return dict(fmt)


def _inspect_bucket(s3, name: str) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name}
    # Encryption
    enc = safe_call(s3.get_bucket_encryption, Bucket=name, default={})
    rules = (enc or {}).get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
    out["encryption"] = [
        r.get("ApplyServerSideEncryptionByDefault", {}) for r in rules
    ]
    # Public access block
    pab = safe_call(s3.get_public_access_block, Bucket=name, default={})
    out["public_access_block"] = (pab or {}).get("PublicAccessBlockConfiguration", {})
    # Lifecycle
    lc = safe_call(s3.get_bucket_lifecycle_configuration, Bucket=name, default={})
    out["lifecycle_rules"] = (lc or {}).get("Rules", []) if lc else []
    # Tags
    tags = safe_call(s3.get_bucket_tagging, Bucket=name, default={})
    out["tags"] = {t["Key"]: t["Value"] for t in (tags or {}).get("TagSet", [])} if tags else {}
    # Replication
    repl = safe_call(s3.get_bucket_replication, Bucket=name, default={})
    out["replication"] = bool(repl)
    # Storage class / format sample (single ListObjectsV2 page)
    listing = safe_call(s3.list_objects_v2, Bucket=name, MaxKeys=SAMPLE_KEYS_PER_BUCKET, default={})
    contents = (listing or {}).get("Contents", []) or []
    out["sample_object_count"] = len(contents)
    keys = [c.get("Key", "") for c in contents]
    out["sample_formats"] = _classify_keys(keys)
    out["sample_storage_classes"] = dict(Counter(c.get("StorageClass", "STANDARD") for c in contents))
    out["sample_keys"] = keys[:25]  # for evidence in report
    # Look for Hive-style partitioning in keys
    out["hive_partition_signals"] = sum(1 for k in keys if "=" in k and "/" in k)
    return out


def collect_s3(region: str, profile: str | None = None) -> dict[str, Any]:
    s3 = make_client("s3", region, profile)
    listing = safe_call(s3.list_buckets, default={"Buckets": []})
    all_buckets = (listing or {}).get("Buckets", [])
    bucket_names = [b["Name"] for b in all_buckets]

    # Filter to candidate lake buckets, capped
    candidates = [n for n in bucket_names if _looks_like_lake(n)][:MAX_DEEP_INSPECT]
    inspected = []
    for n in candidates:
        # Only inspect buckets in this region (or region-less). Cheap region probe.
        loc = safe_call(s3.get_bucket_location, Bucket=n, default=None)
        if loc is None:
            continue
        loc_region = loc.get("LocationConstraint") or "us-east-1"
        if loc_region != region:
            continue
        inspected.append(_inspect_bucket(s3, n))

    return {
        "total_buckets": len(bucket_names),
        "candidate_lake_buckets": len(candidates),
        "inspected": inspected,
        "all_bucket_names": bucket_names,
    }
