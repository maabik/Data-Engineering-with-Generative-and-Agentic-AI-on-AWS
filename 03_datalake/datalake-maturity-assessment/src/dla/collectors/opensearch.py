"""OpenSearch collector — domains and (where readable) vector engine usage."""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


def collect_opensearch(region: str, profile: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"managed_domain_count": 0, "serverless_collection_count": 0, "vector_collections": []}
    try:
        os_client = make_client("opensearch", region, profile)
        domains = safe_call(os_client.list_domain_names, default={"DomainNames": []})
        out["managed_domain_count"] = len((domains or {}).get("DomainNames", []) or [])
        out["managed_domain_names"] = [d.get("DomainName") for d in (domains or {}).get("DomainNames", []) or []]
    except Exception:
        pass

    try:
        oss = make_client("opensearchserverless", region, profile)
        cols = safe_call(oss.list_collections, default={"collectionSummaries": []})
        col_list = (cols or {}).get("collectionSummaries", []) or []
        out["serverless_collection_count"] = len(col_list)
        out["serverless_collection_names"] = [c.get("name") for c in col_list]
        out["vector_collections"] = [c.get("name") for c in col_list if c.get("type") == "VECTORSEARCH"]
    except Exception:
        pass

    return out
