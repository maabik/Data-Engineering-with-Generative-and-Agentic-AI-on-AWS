from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
import yaml


@dataclass
class CompactionThresholds:
    small_file_size_mb: float = 128.0
    small_file_count_threshold: int = 100
    small_file_ratio_threshold: float = 0.5


@dataclass
class SnapshotThresholds:
    max_snapshots: int = 100
    retention_hours: float = 168.0


@dataclass
class OrphanThresholds:
    age_hours: float = 72.0


@dataclass
class QueryLatencyThresholds:
    # p50/p95/p99 latency thresholds in ms that trigger investigation
    p95_warning_ms: float = 5000.0
    p95_critical_ms: float = 15000.0
    degradation_pct: float = 50.0  # % increase vs 7-day baseline triggers flag


@dataclass
class MonitoringConfig:
    mode: str = "on-demand"
    interval_seconds: int = 3600


@dataclass
class AwsConfig:
    region: str = "us-east-1"
    s3_endpoint: str | None = None


@dataclass
class MonitorConfig:
    tables: list[str] = field(default_factory=list)
    compaction: CompactionThresholds = field(default_factory=CompactionThresholds)
    snapshots: SnapshotThresholds = field(default_factory=SnapshotThresholds)
    orphans: OrphanThresholds = field(default_factory=OrphanThresholds)
    query_latency: QueryLatencyThresholds = field(default_factory=QueryLatencyThresholds)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    aws: AwsConfig = field(default_factory=AwsConfig)
    catalog_type: str = "glue"
    catalog_properties: dict[str, Any] = field(default_factory=dict)
    # Claude agent settings
    anthropic_model: str = "claude-sonnet-4-6"
    dry_run: bool = False


class ConfigManager:
    @staticmethod
    def get_default_config() -> MonitorConfig:
        return MonitorConfig()

    @staticmethod
    def load_from_file(path: str) -> MonitorConfig:
        with open(path) as f:
            raw = yaml.safe_load(f)
        return ConfigManager._parse(raw)

    @staticmethod
    def _parse(raw: dict) -> MonitorConfig:
        cfg = MonitorConfig()
        cfg.tables = raw.get("tables", [])

        if "catalog" in raw:
            cfg.catalog_type = raw["catalog"].get("type", "glue")
            cfg.catalog_properties = raw["catalog"].get("properties", {})

        if "thresholds" in raw:
            t = raw["thresholds"]
            if "compaction" in t:
                c = t["compaction"]
                cfg.compaction = CompactionThresholds(
                    small_file_size_mb=c.get("small_file_size_mb", 128.0),
                    small_file_count_threshold=c.get("small_file_count_threshold", 100),
                    small_file_ratio_threshold=c.get("small_file_ratio_threshold", 0.5),
                )
            if "snapshots" in t:
                s = t["snapshots"]
                cfg.snapshots = SnapshotThresholds(
                    max_snapshots=s.get("max_snapshots", 100),
                    retention_hours=s.get("retention_hours", 168.0),
                )
            if "orphans" in t:
                o = t["orphans"]
                cfg.orphans = OrphanThresholds(age_hours=o.get("age_hours", 72.0))

        if "monitoring" in raw:
            m = raw["monitoring"]
            cfg.monitoring = MonitoringConfig(
                mode=m.get("mode", "on-demand"),
                interval_seconds=m.get("interval_seconds", 3600),
            )

        if "aws" in raw:
            a = raw["aws"]
            cfg.aws = AwsConfig(
                region=a.get("region", "us-east-1"),
                s3_endpoint=a.get("s3_endpoint"),
            )

        cfg.dry_run = raw.get("dry_run", False)
        cfg.anthropic_model = raw.get("anthropic_model", "claude-sonnet-4-6")
        return cfg

    @staticmethod
    def validate_config(cfg: MonitorConfig) -> list[str]:
        errors = []
        if not cfg.tables:
            errors.append("No tables configured")
        if cfg.compaction.small_file_size_mb <= 0:
            errors.append("small_file_size_mb must be positive")
        if cfg.compaction.small_file_count_threshold <= 0:
            errors.append("small_file_count_threshold must be positive")
        if cfg.snapshots.retention_hours <= 0:
            errors.append("retention_hours must be positive")
        if cfg.orphans.age_hours <= 0:
            errors.append("orphan age_hours must be positive")
        return errors
