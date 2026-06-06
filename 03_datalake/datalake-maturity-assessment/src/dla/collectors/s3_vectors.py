"""S3 Vectors collector. Service may not be available in all regions; degrade silently."""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


def collect_s3_vectors(region: str, profile: str | None = None) -> dict[str, Any]:
    try:
        client = make_client("s3vectors", region, profile)
    except Exception:
        return {"available": False, "vector_bucket_count": 0, "index_count": 0}

    listing = safe_call(client.list_vector_buckets, default=None)
    if listing is None:
        return {"available": False, "vector_bucket_count": 0, "index_count": 0}

    buckets = (listing or {}).get("vectorBuckets", []) or []
    indexes: list[dict[str, Any]] = []
    for vb in buckets:
        idx = safe_call(client.list_indexes, vectorBucketName=vb.get("vectorBucketName"), default={"indexes": []})
        for i in (idx or {}).get("indexes", []) or []:
            indexes.append({"bucket": vb.get("vectorBucketName"), "name": i.get("indexName")})

    return {
        "available": True,
        "vector_bucket_count": len(buckets),
        "vector_bucket_names": [b.get("vectorBucketName") for b in buckets],
        "index_count": len(indexes),
        "indexes_sample": indexes[:10],
    }
