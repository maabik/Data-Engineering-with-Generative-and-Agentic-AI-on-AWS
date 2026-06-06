"""Maturity dimension scoring modules.

Each module exposes `score(snapshot) -> DimensionScore` with:
  - level: int 1..5
  - evidence: list[str] — bullet points showing what was detected
  - top_gap: str — the single most actionable observation
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DimensionScore:
    name: str
    level: int
    evidence: list[str] = field(default_factory=list)
    top_gap: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "level": self.level, "evidence": self.evidence, "top_gap": self.top_gap}


from .storage_format import score as score_storage_format  # noqa: E402
from .catalog_schema import score as score_catalog_schema  # noqa: E402
from .access_security import score as score_access_security  # noqa: E402
from .quality_observability import score as score_quality_observability  # noqa: E402
from .performance_ingestion import score as score_performance_ingestion  # noqa: E402
from .cost_lifecycle import score as score_cost_lifecycle  # noqa: E402
from .lineage import score as score_lineage  # noqa: E402
from .ai_readiness import score as score_ai_readiness  # noqa: E402

ALL_DIMENSIONS = [
    ("storage_format", score_storage_format),
    ("catalog_schema", score_catalog_schema),
    ("access_security", score_access_security),
    ("quality_observability", score_quality_observability),
    ("performance_ingestion", score_performance_ingestion),
    ("cost_lifecycle", score_cost_lifecycle),
    ("lineage", score_lineage),
    ("ai_readiness", score_ai_readiness),
]

DIMENSION_TITLES = {
    "storage_format": "Storage & Table Format",
    "catalog_schema": "Catalog & Schema Management",
    "access_security": "Access Control & Security",
    "quality_observability": "Data Quality & Observability",
    "performance_ingestion": "Performance & Ingestion",
    "cost_lifecycle": "Cost & Lifecycle Governance",
    "lineage": "Lineage & Provenance",
    "ai_readiness": "AI / Agentic Readiness",
}

DIMENSION_BOOK_REFS = {
    "storage_format": "Chapter 3 — Data Lake Design with Apache Iceberg and S3 Tables",
    "catalog_schema": "Chapter 4 — Data Mesh with Amazon DataZone",
    "access_security": "Chapter 2 — Data Security and Governance",
    "quality_observability": "Chapter 5 — Big Data Processing with AWS Glue and AI Agents",
    "performance_ingestion": "Chapter 5 + Chapter 9 — Streaming and Real-Time Data Processing",
    "cost_lifecycle": "Chapter 2 — Data Security and Governance (FinOps section)",
    "lineage": "Chapter 4 — Data Mesh with Amazon DataZone",
    "ai_readiness": "Chapter 8 — RAG with S3 Vectors + Chapter 12 — Building AI Agents with Bedrock AgentCore",
}
