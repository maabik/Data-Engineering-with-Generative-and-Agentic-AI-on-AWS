"""Remediation catalog — load YAML playbooks, select items based on dimension levels.

Each playbook contains a list of items keyed by `current_level`. We pick the items
whose current_level matches the scored level, set target_level to scored+1 (capped
at 5), and rank by priority_score = impact / effort_weeks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

EFFORT_WEEKS = {"S": 1, "M": 3, "L": 6, "XL": 12}


def _playbook_dir() -> Path:
    return Path(__file__).resolve().parent / "playbooks"


def _load_playbook(dimension: str) -> list[dict[str, Any]]:
    p = _playbook_dir() / f"{dimension}.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    return data.get("items", []) or []


def build_remediation_backlog(scores_by_dim: dict[str, dict[str, Any]], target_level: int = 4) -> list[dict[str, Any]]:
    backlog: list[dict[str, Any]] = []
    seq = 0
    for dim_name, score in scores_by_dim.items():
        cur = score["level"]
        if cur >= target_level:
            continue
        items = _load_playbook(dim_name)
        for item in items:
            if item.get("from_level") != cur:
                continue
            seq += 1
            effort = item.get("effort", "M")
            weeks = EFFORT_WEEKS.get(effort, 3)
            impact = item.get("impact", 3)
            priority = round(impact / max(1, weeks), 2)
            rid = f"REM-{seq:03d}"
            backlog.append({
                "id": rid,
                "dimension": dim_name,
                "current_level": cur,
                "target_level": min(5, cur + 1),
                "title": item["title"],
                "impact": impact,
                "effort": effort,
                "effort_weeks_estimate": weeks,
                "priority_score": priority,
                "aws_services": item.get("aws_services", []),
                "book_reference": item.get("book_reference", ""),
                "why_it_matters": item.get("why_it_matters", ""),
                "steps": item.get("steps", []),
                "acceptance_criteria": item.get("acceptance_criteria", []),
                "reference_code": item.get("reference_code", ""),
            })
    backlog.sort(key=lambda x: x["priority_score"], reverse=True)
    return backlog
