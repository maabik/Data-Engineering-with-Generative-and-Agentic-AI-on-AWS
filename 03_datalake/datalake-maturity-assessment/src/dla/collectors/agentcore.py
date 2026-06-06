"""Bedrock AgentCore collector — gateways, runtimes, identity providers.

AgentCore APIs are still rolling out per-region; we degrade silently if missing.
"""
from __future__ import annotations

from typing import Any

from ._util import make_client, safe_call


_CANDIDATE_SERVICES = ("bedrock-agentcore-control", "bedrock-agentcore")


def collect_agentcore(region: str, profile: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False, "gateway_count": 0, "runtime_count": 0}

    for svc in _CANDIDATE_SERVICES:
        try:
            client = make_client(svc, region, profile)
        except Exception:
            continue
        gw = safe_call(client.list_gateways, default=None) if hasattr(client, "list_gateways") else None
        if gw is not None:
            items = (gw or {}).get("items", []) or (gw or {}).get("gateways", []) or []
            out["available"] = True
            out["gateway_count"] = len(items)
            out["gateway_names"] = [i.get("name") for i in items][:10]
        rt = safe_call(client.list_agent_runtimes, default=None) if hasattr(client, "list_agent_runtimes") else None
        if rt is not None:
            items = (rt or {}).get("agentRuntimes", []) or (rt or {}).get("items", []) or []
            out["available"] = True
            out["runtime_count"] = len(items)
            out["runtime_names"] = [i.get("agentRuntimeName") or i.get("name") for i in items][:10]
        if out["available"]:
            break

    return out
