"""Athena collector — workgroups, recent query history."""
from __future__ import annotations

from typing import Any

from ._util import make_client, paginate, safe_call


def collect_athena(region: str, profile: str | None = None) -> dict[str, Any]:
    client = make_client("athena", region, profile)
    workgroups = paginate(client, "list_work_groups", "WorkGroups")

    wg_details = []
    for wg in workgroups:
        d = safe_call(client.get_work_group, WorkGroup=wg["Name"], default=None)
        if d:
            wg_details.append(d.get("WorkGroup", {}))

    # Recent query stats from primary workgroup
    recent_qids = paginate(client, "list_query_executions", "QueryExecutionIds", WorkGroup="primary", default=[])
    sampled_qids = recent_qids[:50]
    queries_with_partitions = 0
    materialized_view_signals = 0
    total_sampled = 0
    for chunk_start in range(0, len(sampled_qids), 50):
        batch = sampled_qids[chunk_start:chunk_start + 50]
        if not batch:
            break
        result = safe_call(client.batch_get_query_execution, QueryExecutionIds=batch, default={"QueryExecutions": []})
        for q in (result or {}).get("QueryExecutions", []):
            total_sampled += 1
            qstr = (q.get("Query") or "").lower()
            if "partition" in qstr or "where " in qstr:
                queries_with_partitions += 1
            if "create materialized view" in qstr or "refresh materialized view" in qstr:
                materialized_view_signals += 1

    return {
        "workgroup_count": len(workgroups),
        "workgroup_names": [w["Name"] for w in workgroups],
        "workgroup_result_encryption": [
            (w.get("Configuration", {}) or {}).get("ResultConfiguration", {}).get("EncryptionConfiguration", {})
            for w in wg_details
        ],
        "recent_query_count": len(recent_qids),
        "sampled_query_count": total_sampled,
        "queries_with_filters": queries_with_partitions,
        "materialized_view_signals": materialized_view_signals,
    }
