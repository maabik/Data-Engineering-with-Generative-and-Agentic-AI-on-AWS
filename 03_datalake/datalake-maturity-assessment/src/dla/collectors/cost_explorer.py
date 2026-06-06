"""Cost Explorer collector — cost allocation tags, anomaly subscriptions, S3 spend."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ._util import make_client, safe_call


def collect_cost_explorer(region: str, profile: str | None = None) -> dict[str, Any]:
    # CE is global but the SDK pins to us-east-1
    ce = make_client("ce", "us-east-1", profile)

    cat_resp = safe_call(ce.list_cost_allocation_tags, default={"CostAllocationTags": []})
    tags = (cat_resp or {}).get("CostAllocationTags", []) or []
    active_tags = [t for t in tags if t.get("Status") == "Active"]

    # Anomaly monitors / subscriptions
    monitors = safe_call(ce.get_anomaly_monitors, default={"AnomalyMonitors": []})
    subs = safe_call(ce.get_anomaly_subscriptions, default={"AnomalySubscriptions": []})

    # S3 spend last 30 days
    today = date.today()
    start = (today - timedelta(days=30)).isoformat()
    end = today.isoformat()
    cost_resp = safe_call(
        ce.get_cost_and_usage,
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Simple Storage Service"]}},
        default=None,
    )
    s3_cost_30d = 0.0
    if cost_resp:
        for r in cost_resp.get("ResultsByTime", []) or []:
            try:
                s3_cost_30d += float(r["Total"]["UnblendedCost"]["Amount"])
            except (KeyError, ValueError):
                pass

    return {
        "active_cost_allocation_tag_count": len(active_tags),
        "active_cost_allocation_tags": [t.get("TagKey") for t in active_tags],
        "anomaly_monitor_count": len((monitors or {}).get("AnomalyMonitors", []) or []),
        "anomaly_subscription_count": len((subs or {}).get("AnomalySubscriptions", []) or []),
        "s3_cost_last_30d_usd": round(s3_cost_30d, 2),
    }
