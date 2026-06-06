"""JSON report renderer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def render_json(account_id: str, region: str, summary: dict[str, Any], backlog: list[dict[str, Any]], snapshot: dict[str, Any] | None = None) -> str:
    payload = {
        "schema_version": "1",
        "account_id": account_id,
        "region": region,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "remediation": backlog,
    }
    if snapshot is not None:
        payload["snapshot"] = snapshot
    return json.dumps(payload, indent=2, default=str)
