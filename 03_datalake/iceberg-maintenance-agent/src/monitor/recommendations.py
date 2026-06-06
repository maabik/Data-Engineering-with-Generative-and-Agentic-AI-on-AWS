from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monitor.config import MonitorConfig
    from monitor.metrics import TableMetrics


class RecommendationType(Enum):
    COMPACTION = "compaction"
    SNAPSHOT_EXPIRATION = "snapshot_expiration"
    ORPHAN_CLEANUP = "orphan_cleanup"
    QUERY_OPTIMIZATION = "query_optimization"


class Urgency(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    def __lt__(self, other: "Urgency") -> bool:
        order = [Urgency.LOW, Urgency.MEDIUM, Urgency.HIGH, Urgency.CRITICAL]
        return order.index(self) < order.index(other)

    def __le__(self, other: "Urgency") -> bool:
        return self == other or self < other

    def __gt__(self, other: "Urgency") -> bool:
        return not self <= other

    def __ge__(self, other: "Urgency") -> bool:
        return not self < other


@dataclass
class Recommendation:
    recommendation_type: RecommendationType
    urgency: Urgency
    reason: str
    estimated_benefit: str
    action_params: dict = None

    def __post_init__(self):
        if self.action_params is None:
            self.action_params = {}


class RecommendationEngine:
    def __init__(self, config: "MonitorConfig"):
        self.config = config

    def generate_recommendations(self, metrics: "TableMetrics") -> list[Recommendation]:
        recs: list[Recommendation] = []

        recs.extend(self._check_compaction(metrics))
        recs.extend(self._check_snapshots(metrics))
        recs.extend(self._check_orphans(metrics))
        recs.extend(self._check_query_latency(metrics))

        recs.sort(key=lambda r: r.urgency, reverse=True)
        return recs

    def _check_compaction(self, metrics: "TableMetrics") -> list[Recommendation]:
        fm = metrics.file_metrics
        ct = self.config.compaction
        recs = []

        reasons = []
        if fm.small_files > ct.small_file_count_threshold:
            reasons.append(f"{fm.small_files} small files (>{ct.small_file_count_threshold})")
        if fm.small_file_ratio > ct.small_file_ratio_threshold:
            reasons.append(f"{fm.small_file_ratio*100:.0f}% small file ratio (>{ct.small_file_ratio_threshold*100:.0f}%)")
        if 0 < fm.average_file_size_mb < ct.small_file_size_mb:
            reasons.append(f"avg file size {fm.average_file_size_mb:.1f}MB (<{ct.small_file_size_mb}MB target)")

        if not reasons:
            return recs

        if fm.small_file_ratio > 0.8 or fm.small_files > ct.small_file_count_threshold * 3:
            urgency = Urgency.CRITICAL
        elif fm.small_file_ratio > 0.6 or fm.small_files > ct.small_file_count_threshold * 2:
            urgency = Urgency.HIGH
        elif fm.small_file_ratio > ct.small_file_ratio_threshold:
            urgency = Urgency.MEDIUM
        else:
            urgency = Urgency.LOW

        recs.append(Recommendation(
            recommendation_type=RecommendationType.COMPACTION,
            urgency=urgency,
            reason="; ".join(reasons),
            estimated_benefit=f"Reduce file count from {fm.total_files} to ~{max(1, fm.total_files // 10)}, improving scan performance",
            action_params={"target_file_size_mb": ct.small_file_size_mb},
        ))
        return recs

    def _check_snapshots(self, metrics: "TableMetrics") -> list[Recommendation]:
        sm = metrics.snapshot_metrics
        st = self.config.snapshots
        recs = []

        reasons = []
        if sm.total_snapshots > st.max_snapshots:
            reasons.append(f"{sm.total_snapshots} snapshots (max {st.max_snapshots})")
        if sm.snapshots_to_expire > 0:
            reasons.append(f"{sm.snapshots_to_expire} snapshots older than {st.retention_hours}h retention")

        if not reasons:
            return recs

        if sm.total_snapshots > st.max_snapshots * 2:
            urgency = Urgency.HIGH
        elif sm.total_snapshots > st.max_snapshots:
            urgency = Urgency.MEDIUM
        else:
            urgency = Urgency.LOW

        recs.append(Recommendation(
            recommendation_type=RecommendationType.SNAPSHOT_EXPIRATION,
            urgency=urgency,
            reason="; ".join(reasons),
            estimated_benefit=f"Remove ~{sm.snapshots_to_expire} snapshots, saving ~{sm.estimated_metadata_reduction_mb:.0f}MB metadata",
            action_params={
                "retain_last": min(st.max_snapshots, sm.total_snapshots - sm.snapshots_to_expire),
                "max_snapshot_age_ms": int(st.retention_hours * 3600 * 1000),
            },
        ))
        return recs

    def _check_orphans(self, metrics: "TableMetrics") -> list[Recommendation]:
        om = metrics.orphan_metrics
        recs = []

        if om.orphan_file_count == 0:
            return recs

        if om.orphan_total_size_mb > 10_000 or om.orphan_file_count > 500:
            urgency = Urgency.HIGH
        elif om.orphan_total_size_mb > 1_000 or om.orphan_file_count > 100:
            urgency = Urgency.MEDIUM
        else:
            urgency = Urgency.LOW

        recs.append(Recommendation(
            recommendation_type=RecommendationType.ORPHAN_CLEANUP,
            urgency=urgency,
            reason=f"{om.orphan_file_count} orphan files ({om.orphan_total_size_mb:.0f}MB)",
            estimated_benefit=f"Reclaim {om.orphan_total_size_mb:.0f}MB of storage",
            action_params={"older_than_ms": int(self.config.orphans.age_hours * 3600 * 1000)},
        ))
        return recs

    def _check_query_latency(self, metrics: "TableMetrics") -> list[Recommendation]:
        ql = metrics.query_latency
        qt = self.config.query_latency
        recs = []

        if ql.sample_count == 0:
            return recs

        reasons = []
        if ql.p95_ms >= qt.p95_critical_ms:
            reasons.append(f"p95 latency {ql.p95_ms:.0f}ms exceeds critical threshold {qt.p95_critical_ms:.0f}ms")
        elif ql.p95_ms >= qt.p95_warning_ms:
            reasons.append(f"p95 latency {ql.p95_ms:.0f}ms exceeds warning threshold {qt.p95_warning_ms:.0f}ms")

        if ql.degradation_pct >= qt.degradation_pct:
            reasons.append(f"latency degraded {ql.degradation_pct:.0f}% vs 7-day baseline")

        if not reasons:
            return recs

        urgency = Urgency.CRITICAL if ql.p95_ms >= qt.p95_critical_ms else Urgency.HIGH

        recs.append(Recommendation(
            recommendation_type=RecommendationType.QUERY_OPTIMIZATION,
            urgency=urgency,
            reason="; ".join(reasons),
            estimated_benefit="Compaction and/or snapshot expiration should reduce scan overhead and improve query latency",
            action_params={"p95_ms": ql.p95_ms, "baseline_p95_ms": ql.baseline_p95_ms},
        ))
        return recs
