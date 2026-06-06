"""CloudTrail collector — trail count + S3/data-event coverage."""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


def collect_cloudtrail(region: str, profile: str | None = None) -> dict[str, Any]:
    ct = make_client("cloudtrail", region, profile)
    trails = safe_call(ct.describe_trails, includeShadowTrails=False, default={"trailList": []})
    trail_list = (trails or {}).get("trailList", []) or []
    multi_region = sum(1 for t in trail_list if t.get("IsMultiRegionTrail"))
    org_trail = sum(1 for t in trail_list if t.get("IsOrganizationTrail"))
    return {
        "trail_count": len(trail_list),
        "multi_region_trail_count": multi_region,
        "org_trail_count": org_trail,
        "trail_names": [t.get("Name") for t in trail_list],
    }
