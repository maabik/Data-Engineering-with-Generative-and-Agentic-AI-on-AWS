"""Scoring aggregation rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_WEIGHTS = {
    "storage_format": 1.0,
    "catalog_schema": 1.0,
    "access_security": 1.0,
    "quality_observability": 1.0,
    "performance_ingestion": 1.0,
    "cost_lifecycle": 1.0,
    "lineage": 1.0,
    "ai_readiness": 1.0,
}


def load_weights(preset: str | None = None) -> dict[str, float]:
    if not preset:
        return DEFAULT_WEIGHTS.copy()
    here = Path(__file__).resolve().parents[3]
    p = here / "config" / "industry_presets" / f"{preset}.yaml"
    if not p.exists():
        p = here / "config" / f"{preset}.yaml"
    if not p.exists():
        return DEFAULT_WEIGHTS.copy()
    data = yaml.safe_load(p.read_text()) or {}
    weights = data.get("weights", {})
    out = DEFAULT_WEIGHTS.copy()
    out.update({k: float(v) for k, v in weights.items()})
    return out


LEVEL_LABELS = {
    1: "Ad hoc",
    2: "Repeatable",
    3: "Defined",
    4: "Managed",
    5: "Optimized",
}


def aggregate(dimension_scores: list, weights: dict[str, float]) -> dict[str, Any]:
    """dimension_scores: list[DimensionScore]"""
    by_name = {d.name: d for d in dimension_scores}
    total_weight = 0.0
    weighted_sum = 0.0
    for name, w in weights.items():
        if name in by_name:
            weighted_sum += by_name[name].level * w
            total_weight += w
    weighted = weighted_sum / total_weight if total_weight else 0.0
    weakest_level = min((d.level for d in dimension_scores), default=0)
    weakest = [d.name for d in dimension_scores if d.level == weakest_level]

    return {
        "weighted_score": round(weighted, 2),
        "weighted_label": LEVEL_LABELS.get(round(weighted), "?"),
        "weakest_level": weakest_level,
        "weakest_dimensions": weakest,
        "scores_by_dimension": {d.name: d.to_dict() for d in dimension_scores},
    }
