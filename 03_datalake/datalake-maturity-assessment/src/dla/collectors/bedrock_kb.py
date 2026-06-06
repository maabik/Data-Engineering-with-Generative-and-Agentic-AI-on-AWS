"""Bedrock Knowledge Bases collector."""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


def collect_bedrock_kb(region: str, profile: str | None = None) -> dict[str, Any]:
    try:
        client = make_client("bedrock-agent", region, profile)
    except Exception:
        return {"available": False, "kb_count": 0, "data_source_count": 0}

    listing = safe_call(client.list_knowledge_bases, default={"knowledgeBaseSummaries": []})
    if listing is None:
        return {"available": False, "kb_count": 0, "data_source_count": 0}
    kbs = (listing or {}).get("knowledgeBaseSummaries", []) or []

    data_sources = []
    for kb in kbs:
        kb_id = kb.get("knowledgeBaseId")
        if not kb_id:
            continue
        ds_resp = safe_call(client.list_data_sources, knowledgeBaseId=kb_id, default={"dataSourceSummaries": []})
        for ds in (ds_resp or {}).get("dataSourceSummaries", []) or []:
            data_sources.append({"kb_id": kb_id, "kb_name": kb.get("name"), "ds_name": ds.get("name"), "status": ds.get("status")})

    # Agents — reused for AI-readiness signal
    agents = safe_call(client.list_agents, default={"agentSummaries": []})
    agent_summaries = (agents or {}).get("agentSummaries", []) or []

    return {
        "available": True,
        "kb_count": len(kbs),
        "kb_names": [k.get("name") for k in kbs],
        "data_source_count": len(data_sources),
        "data_sources_sample": data_sources[:10],
        "agent_count": len(agent_summaries),
        "agent_names": [a.get("agentName") for a in agent_summaries][:10],
    }
