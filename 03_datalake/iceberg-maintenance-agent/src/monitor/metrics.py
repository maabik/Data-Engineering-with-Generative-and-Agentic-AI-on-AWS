from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FileMetrics:
    total_files: int = 0
    small_files: int = 0
    average_file_size_mb: float = 0.0
    median_file_size_mb: float = 0.0
    total_size_mb: float = 0.0
    file_size_distribution: dict[str, int] = field(default_factory=dict)

    @property
    def small_file_ratio(self) -> float:
        if self.total_files == 0:
            return 0.0
        return self.small_files / self.total_files


@dataclass
class SnapshotMetrics:
    total_snapshots: int = 0
    oldest_snapshot_age_hours: float = 0.0
    newest_snapshot_age_hours: float = 0.0
    snapshots_to_expire: int = 0
    estimated_metadata_reduction_mb: float = 0.0


@dataclass
class OrphanMetrics:
    orphan_file_count: int = 0
    orphan_total_size_mb: float = 0.0
    orphan_file_paths: list[str] = field(default_factory=list)


@dataclass
class QueryLatencyMetrics:
    """Athena / Glue query latency metrics for the table."""
    sample_count: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    avg_ms: float = 0.0
    baseline_p95_ms: float = 0.0   # 7-day rolling baseline
    degradation_pct: float = 0.0   # vs baseline
    query_engine: str = "athena"


@dataclass
class TableMetrics:
    table_location: str = ""
    snapshot_id: str = ""
    analysis_timestamp: str = ""
    file_metrics: FileMetrics = field(default_factory=FileMetrics)
    snapshot_metrics: SnapshotMetrics = field(default_factory=SnapshotMetrics)
    orphan_metrics: OrphanMetrics = field(default_factory=OrphanMetrics)
    query_latency: QueryLatencyMetrics = field(default_factory=QueryLatencyMetrics)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TableMetrics":
        return cls(
            table_location=d.get("table_location", ""),
            snapshot_id=d.get("snapshot_id", ""),
            analysis_timestamp=d.get("analysis_timestamp", ""),
            file_metrics=FileMetrics(**d.get("file_metrics", {})),
            snapshot_metrics=SnapshotMetrics(**d.get("snapshot_metrics", {})),
            orphan_metrics=OrphanMetrics(**d.get("orphan_metrics", {})),
            query_latency=QueryLatencyMetrics(**d.get("query_latency", {})),
        )
