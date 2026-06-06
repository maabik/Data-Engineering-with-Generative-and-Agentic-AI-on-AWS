"""Amazon DataZone collector — domains, projects, glossaries, lineage signal."""
from __future__ import annotations

from typing import Any

from ._util import make_client, paginate, safe_call


def collect_datazone(region: str, profile: str | None = None) -> dict[str, Any]:
    try:
        dz = make_client("datazone", region, profile)
    except Exception:
        return {"available": False, "domain_count": 0}

    domains_resp = safe_call(dz.list_domains, default=None)
    if domains_resp is None:
        return {"available": False, "domain_count": 0}

    domains = (domains_resp or {}).get("items", []) or []
    project_count = 0
    asset_count = 0
    glossary_count = 0
    for d in domains:
        d_id = d.get("id")
        if not d_id:
            continue
        projects = paginate(dz, "list_projects", "items", domainIdentifier=d_id)
        project_count += len(projects)
        # Glossaries are per-project — sample first project only to bound calls
        for p in projects[:3]:
            glossaries = safe_call(dz.search, domainIdentifier=d_id, searchScope="GLOSSARY", maxResults=50, default={"items": []})
            glossary_count += len((glossaries or {}).get("items", []) or [])
            assets = safe_call(dz.search, domainIdentifier=d_id, searchScope="ASSET", maxResults=50, default={"items": []})
            asset_count += len((assets or {}).get("items", []) or [])

    return {
        "available": True,
        "domain_count": len(domains),
        "domain_names": [d.get("name") for d in domains],
        "project_count": project_count,
        "asset_count": asset_count,
        "glossary_count": glossary_count,
    }
