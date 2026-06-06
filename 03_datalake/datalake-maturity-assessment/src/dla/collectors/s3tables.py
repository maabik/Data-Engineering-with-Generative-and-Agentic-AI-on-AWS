"""S3 Tables collector — table buckets, namespaces, tables (managed Iceberg)."""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


def collect_s3tables(region: str, profile: str | None = None) -> dict[str, Any]:
    try:
        client = make_client("s3tables", region, profile)
    except Exception:
        return {"available": False, "table_bucket_count": 0, "table_count": 0}

    listing = safe_call(client.list_table_buckets, default={"tableBuckets": []})
    if listing is None:
        return {"available": False, "table_bucket_count": 0, "table_count": 0}
    table_buckets = (listing or {}).get("tableBuckets", []) or []

    namespaces: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for tb in table_buckets:
        arn = tb.get("arn")
        ns_resp = safe_call(client.list_namespaces, tableBucketARN=arn, default={"namespaces": []})
        for ns in (ns_resp or {}).get("namespaces", []) or []:
            namespaces.append({"bucket": tb.get("name"), "namespace": ns.get("namespace")})
            tbl_resp = safe_call(
                client.list_tables,
                tableBucketARN=arn,
                namespace=ns.get("namespace", [None])[0] if isinstance(ns.get("namespace"), list) else ns.get("namespace"),
                default={"tables": []},
            )
            for t in (tbl_resp or {}).get("tables", []) or []:
                tables.append({
                    "bucket": tb.get("name"),
                    "namespace": t.get("namespace"),
                    "name": t.get("name"),
                    "type": t.get("type"),
                })

    return {
        "available": True,
        "table_bucket_count": len(table_buckets),
        "table_bucket_names": [tb.get("name") for tb in table_buckets],
        "namespace_count": len(namespaces),
        "table_count": len(tables),
        "tables_sample": tables[:10],
    }
