from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monitor.config import MonitorConfig

from monitor.analyzer import TableAnalyzer
from monitor.executor import MaintenanceExecutor
from monitor.recommendations import RecommendationEngine
from monitor.reporter import Reporter

logger = logging.getLogger(__name__)


class MonitorEngine:
    """
    Non-agent (rule-based) monitoring engine.  Analyses tables and optionally
    executes maintenance based on deterministic recommendation rules, without
    calling Claude.  Use the CLI `agent` sub-command for the AI-powered variant.
    """

    def __init__(self, config: "MonitorConfig"):
        self.config = config
        self.analyzer = TableAnalyzer(config)
        self.rec_engine = RecommendationEngine(config)
        self.executor = MaintenanceExecutor(config)
        self.reporter = Reporter()

    def run_once(self, execute: bool = False) -> str:
        reports = []
        for table in self.config.tables:
            try:
                metrics = self.analyzer.analyze_table(table)
                recs = self.rec_engine.generate_recommendations(metrics)
                report = self.reporter.generate_report(metrics, recs)

                if execute and recs:
                    actions = self.executor.execute(table, recs)
                    report.actions_taken = actions

                reports.append(report)
                logger.info("Finished analysis for %s", table)
            except Exception as exc:
                logger.error("Failed to analyse %s: %s", table, exc)

        return self.reporter.generate_summary_report(reports)

    def run_continuous(self, execute: bool = False) -> None:
        interval = self.config.monitoring.interval_seconds
        logger.info("Starting continuous monitoring (interval=%ss)", interval)
        while True:
            summary = self.run_once(execute=execute)
            print(summary)
            time.sleep(interval)
