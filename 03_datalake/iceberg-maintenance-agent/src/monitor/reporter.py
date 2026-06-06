from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monitor.metrics import TableMetrics
    from monitor.recommendations import Recommendation


@dataclass
class MaintenanceReport:
    table_location: str
    analysis_timestamp: str
    metrics_summary: dict
    recommendations: list["Recommendation"] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "table_location": self.table_location,
            "analysis_timestamp": self.analysis_timestamp,
            "metrics_summary": self.metrics_summary,
            "recommendations": [
                {
                    "type": r.recommendation_type.value,
                    "urgency": r.urgency.value,
                    "reason": r.reason,
                    "estimated_benefit": r.estimated_benefit,
                }
                for r in self.recommendations
            ],
            "actions_taken": self.actions_taken,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_text(self) -> str:
        lines = [
            f"Table: {self.table_location}",
            f"Analyzed: {self.analysis_timestamp}",
            "",
        ]

        ms = self.metrics_summary
        if "file_metrics" in ms:
            fm = ms["file_metrics"]
            lines += [
                "File Metrics:",
                f"  Total files    : {fm.get('total_files', 0)}",
                f"  Small files    : {fm.get('small_files', 0)} ({fm.get('small_file_ratio', 0)*100:.1f}%)",
                f"  Avg file size  : {fm.get('average_file_size_mb', 0):.1f} MB",
                f"  Total size     : {fm.get('total_size_mb', 0):.1f} MB",
                "",
            ]

        if "snapshot_metrics" in ms:
            sm = ms["snapshot_metrics"]
            lines += [
                "Snapshot Metrics:",
                f"  Total snapshots: {sm.get('total_snapshots', 0)}",
                f"  Oldest age     : {sm.get('oldest_snapshot_age_hours', 0):.0f}h",
                f"  To expire      : {sm.get('snapshots_to_expire', 0)}",
                "",
            ]

        if "orphan_metrics" in ms:
            om = ms["orphan_metrics"]
            lines += [
                "Orphan Files:",
                f"  Count : {om.get('orphan_file_count', 0)}",
                f"  Size  : {om.get('orphan_total_size_mb', 0):.1f} MB",
                "",
            ]

        if "query_latency" in ms:
            ql = ms["query_latency"]
            if ql.get("sample_count", 0) > 0:
                lines += [
                    "Query Latency:",
                    f"  p50 : {ql.get('p50_ms', 0):.0f}ms",
                    f"  p95 : {ql.get('p95_ms', 0):.0f}ms",
                    f"  p99 : {ql.get('p99_ms', 0):.0f}ms",
                    f"  vs baseline: {ql.get('degradation_pct', 0):+.0f}%",
                    "",
                ]

        if self.recommendations:
            lines.append("Recommendations:")
            for r in self.recommendations:
                lines.append(f"  [{r.urgency.value.upper()}] {r.recommendation_type.value}")
                lines.append(f"    Reason  : {r.reason}")
                lines.append(f"    Benefit : {r.estimated_benefit}")
                lines.append("")
        else:
            lines.append("No maintenance needed. Table is healthy.")
            lines.append("")

        if self.actions_taken:
            lines.append("Actions Taken:")
            for a in self.actions_taken:
                lines.append(f"  ✓ {a}")
            lines.append("")

        return "\n".join(lines)


class Reporter:
    def generate_report(
        self,
        metrics: "TableMetrics",
        recommendations: list["Recommendation"],
    ) -> MaintenanceReport:
        return MaintenanceReport(
            table_location=metrics.table_location,
            analysis_timestamp=metrics.analysis_timestamp or datetime.utcnow().isoformat(),
            metrics_summary=metrics.to_dict(),
            recommendations=recommendations,
        )

    def generate_summary_report(self, reports: list[MaintenanceReport]) -> str:
        lines = [
            "=" * 70,
            "ICEBERG MAINTENANCE SUMMARY",
            f"Generated: {datetime.utcnow().isoformat()}Z",
            f"Tables analyzed: {len(reports)}",
            "=" * 70,
            "",
        ]

        tables_needing_work = [r for r in reports if r.recommendations]
        healthy = [r for r in reports if not r.recommendations]

        lines.append(f"Healthy tables   : {len(healthy)}")
        lines.append(f"Need maintenance : {len(tables_needing_work)}")
        lines.append("")

        if tables_needing_work:
            lines.append("Tables Needing Maintenance:")
            for r in tables_needing_work:
                urgencies = [rec.urgency.value.upper() for rec in r.recommendations]
                types = [rec.recommendation_type.value for rec in r.recommendations]
                lines.append(f"  {r.table_location}")
                lines.append(f"    Actions: {', '.join(types)}")
                lines.append(f"    Urgency: {', '.join(urgencies)}")
                lines.append("")

        if healthy:
            lines.append("Healthy Tables:")
            for r in healthy:
                lines.append(f"  ✓ {r.table_location}")
            lines.append("")

        return "\n".join(lines)
