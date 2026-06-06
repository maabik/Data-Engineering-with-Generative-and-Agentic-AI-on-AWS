"""Cost & Lifecycle Governance dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    s3 = snapshot.get("s3", {}) or {}
    ce = snapshot.get("cost_explorer", {}) or {}

    inspected = s3.get("inspected", []) or []
    total = max(1, len(inspected))
    tagged = sum(1 for b in inspected if b.get("tags"))
    has_lc = sum(1 for b in inspected if b.get("lifecycle_rules"))
    int_tier = 0
    glacier_tier = 0
    for b in inspected:
        for r in b.get("lifecycle_rules", []) or []:
            for t in r.get("Transitions", []) or []:
                sc = t.get("StorageClass", "")
                if sc == "INTELLIGENT_TIERING":
                    int_tier += 1
                if sc in ("GLACIER", "GLACIER_IR", "DEEP_ARCHIVE"):
                    glacier_tier += 1

    active_tags = ce.get("active_cost_allocation_tags", []) or []
    cost_tag_keys = {t.lower() for t in active_tags}
    has_dataproduct_tag = any(
        k in cost_tag_keys for k in ("dataproduct", "data_product", "data-product", "businessunit", "business_unit", "owner", "costcenter", "cost_center")
    )
    anomaly_subs = ce.get("anomaly_subscription_count", 0)
    s3_30d = ce.get("s3_cost_last_30d_usd", 0.0)

    evidence = [
        f"Lake buckets inspected: {len(inspected)}, tagged: {tagged}, with lifecycle rules: {has_lc}",
        f"Lifecycle transitions: Intelligent-Tiering={int_tier}, Glacier-family={glacier_tier}",
        f"Active cost allocation tags: {len(active_tags)} ({'data-product/owner present' if has_dataproduct_tag else 'no data-product/owner tags activated'})",
        f"Cost anomaly subscriptions: {anomaly_subs}; last 30d S3 spend ≈ ${s3_30d:.2f}",
    ]

    level = 1
    if tagged >= 0.3 * total or has_lc >= 0.3 * total or int_tier:
        level = 2
    if has_dataproduct_tag and (glacier_tier or has_lc >= 0.5 * total):
        level = 3
    if level >= 3 and has_lc >= 0.7 * total:
        level = 4
    if level >= 4 and anomaly_subs > 0 and len(active_tags) >= 3:
        level = 5

    if level == 1:
        top_gap = "Add bucket tagging and lifecycle rules; turn on Intelligent-Tiering for cold prefixes."
    elif level == 2:
        top_gap = "Activate cost allocation tags (DataProduct, Owner, BusinessUnit) and apply them to lake buckets/tables."
    elif level == 3:
        top_gap = "Roll lifecycle rules out to ≥70% of lake buckets; add Glacier-IR transitions for cold data."
    elif level == 4:
        top_gap = "Subscribe to Cost Anomaly Detection on lake spend and chargeback by tag."
    else:
        top_gap = "—"

    return DimensionScore(name="cost_lifecycle", level=level, evidence=evidence, top_gap=top_gap)
