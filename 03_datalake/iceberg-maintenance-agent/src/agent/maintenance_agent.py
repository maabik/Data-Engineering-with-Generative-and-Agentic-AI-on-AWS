"""
Iceberg Maintenance Agent — powered by Claude on Amazon Bedrock.

The agent receives a list of table locations, collects metrics for each,
then uses Claude (via Bedrock Converse API) to decide which maintenance
operations to run and in what order.  The actual operations are performed
by MaintenanceExecutor via tool calls that Claude issues.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import boto3

from monitor.analyzer import TableAnalyzer
from monitor.config import MonitorConfig
from monitor.executor import MaintenanceExecutor
from monitor.metrics import TableMetrics
from monitor.recommendations import RecommendationEngine, RecommendationType
from monitor.reporter import MaintenanceReport, Reporter

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Tool definitions — Bedrock Converse toolSpec format
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "toolSpec": {
            "name": "analyze_table",
            "description": (
                "Collect health metrics for an Iceberg table: file counts and sizes, "
                "snapshot count and age, orphan files, and recent query latency from Athena. "
                "Returns a JSON object with all metrics and pre-computed recommendations."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_location": {
                            "type": "string",
                            "description": "S3 URI of the Iceberg table, e.g. s3://bucket/warehouse/db/events",
                        }
                    },
                    "required": ["table_location"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "run_compaction",
            "description": (
                "Rewrite small data files in an Iceberg table into larger, optimally-sized files. "
                "Improves query scan performance by reducing file count."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_location": {"type": "string"},
                        "target_file_size_mb": {
                            "type": "number",
                            "description": "Target output file size in MB (default: 128)",
                        },
                    },
                    "required": ["table_location"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "expire_snapshots",
            "description": (
                "Remove old Iceberg snapshots that are no longer needed for time travel, "
                "freeing metadata storage and reducing planning overhead."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_location": {"type": "string"},
                        "retain_last": {
                            "type": "integer",
                            "description": "Number of most-recent snapshots to keep (default: 10)",
                        },
                        "older_than_hours": {
                            "type": "number",
                            "description": "Expire snapshots older than this many hours (default: 168)",
                        },
                    },
                    "required": ["table_location"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "delete_orphan_files",
            "description": (
                "Delete S3 files that are not referenced by any Iceberg snapshot. "
                "Reclaims wasted storage space."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_location": {"type": "string"},
                        "older_than_hours": {
                            "type": "number",
                            "description": "Only delete orphans older than this many hours (default: 72)",
                        },
                    },
                    "required": ["table_location"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "generate_report",
            "description": (
                "Generate a human-readable maintenance report for one or more tables, "
                "summarising metrics, recommendations, and actions taken."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "table_locations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of table locations to include in the report",
                        }
                    },
                    "required": ["table_locations"],
                }
            },
        }
    },
]

SYSTEM_PROMPT = """\
You are an expert Apache Iceberg data-platform engineer responsible for keeping \
Iceberg tables healthy and performant.

Your job:
1. Use `analyze_table` to collect metrics for each table you are given.
2. Inspect the metrics carefully — pay particular attention to:
   - File count and small-file ratio (compaction signal)
   - Snapshot count and age (expiration signal)
   - Orphan file count and size (cleanup signal)
   - Query latency p95 and any degradation vs the 7-day baseline
3. Decide which maintenance operations are necessary. Prioritise CRITICAL and \
HIGH urgency issues. Skip operations that are not needed.
4. Execute the required operations using the provided tools.
5. Finally, call `generate_report` to produce a summary of all tables.

Reasoning guidelines:
- If a table has high p95 latency AND many small files, compaction is likely \
the root cause — run it first.
- If p95 latency is high but files are healthy, snapshot metadata overhead may \
be the culprit — expire snapshots.
- Only delete orphan files if they are old enough (default 72 h) to avoid \
interfering with in-flight writes.
- If dry_run is true (noted in the user message), all operations are \
simulated — still explain what would happen and call the tools.
- Be concise in your tool calls; one tool call per operation per table.
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class IcebergMaintenanceAgent:
    """
    Agentic loop using Amazon Bedrock Converse API with tool use.

    1. Sends a system prompt + table list to Claude on Bedrock.
    2. Claude calls tools to analyse tables and issue maintenance commands.
    3. The loop executes each tool call and feeds results back.
    4. Continues until Claude produces a final text response (no more tool calls).
    """

    def __init__(self, config: MonitorConfig):
        self.config = config
        self.analyzer = TableAnalyzer(config)
        self.executor = MaintenanceExecutor(config)
        self.rec_engine = RecommendationEngine(config)
        self.reporter = Reporter()
        self.bedrock = boto3.client("bedrock-runtime", region_name=config.aws.region)

        self._metrics_cache: dict[str, TableMetrics] = {}
        self._reports: list[MaintenanceReport] = []
        self._actions_taken: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self, tables: list[str] | None = None) -> str:
        tables = tables or self.config.tables
        if not tables:
            raise ValueError("No tables specified")

        logger.info("Starting maintenance agent for %d tables", len(tables))

        model_id = self.config.anthropic_model
        # Ensure we use the cross-region inference profile format
        if not model_id.startswith(("us.", "eu.", "ap.")):
            model_id = f"us.{model_id}"

        user_text = (
            f"Today is {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
            f"Please analyse and maintain the following Iceberg tables:\n"
            + "\n".join(f"- {t}" for t in tables)
            + ("\n\nNOTE: dry_run=true — simulate all operations, do not make real changes." if self.config.dry_run else "")
        )

        messages: list[dict] = [{"role": "user", "content": [{"text": user_text}]}]
        final_text = ""

        # Agentic loop
        while True:
            response = self.bedrock.converse(
                modelId=model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig={"tools": TOOLS},
                inferenceConfig={"maxTokens": 4096},
            )

            output_msg = response["output"]["message"]
            stop_reason = response["stopReason"]

            # Collect text blocks from this turn
            for block in output_msg.get("content", []):
                if "text" in block:
                    final_text = block["text"]
                    logger.info("[Claude] %s", block["text"][:300])

            # Append assistant message to history
            messages.append(output_msg)

            if stop_reason != "tool_use":
                break

            # Execute tool calls and collect results
            tool_results = []
            for block in output_msg.get("content", []):
                if "toolUse" not in block:
                    continue
                tool_use = block["toolUse"]
                name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                logger.info("Tool call: %s(%s)", name, json.dumps(tool_input)[:200])
                result = self._execute_tool(name, tool_input)

                tool_results.append({
                    "toolUseId": tool_use_id,
                    "content": [{"json": result}],
                })

            messages.append({
                "role": "user",
                "content": [{"toolResult": tr} for tr in tool_results],
            })

        return final_text

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------
    def _execute_tool(self, name: str, inputs: dict) -> Any:
        try:
            if name == "analyze_table":
                return self._tool_analyze_table(**inputs)
            if name == "run_compaction":
                return self._tool_run_compaction(**inputs)
            if name == "expire_snapshots":
                return self._tool_expire_snapshots(**inputs)
            if name == "delete_orphan_files":
                return self._tool_delete_orphan_files(**inputs)
            if name == "generate_report":
                return self._tool_generate_report(**inputs)
            return {"error": f"Unknown tool: {name}"}
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------
    def _tool_analyze_table(self, table_location: str) -> dict:
        metrics = self.analyzer.analyze_table(table_location)
        self._metrics_cache[table_location] = metrics
        recs = self.rec_engine.generate_recommendations(metrics)
        result = metrics.to_dict()
        result["recommendations"] = [
            {
                "type": r.recommendation_type.value,
                "urgency": r.urgency.value,
                "reason": r.reason,
                "estimated_benefit": r.estimated_benefit,
            }
            for r in recs
        ]
        return result

    def _tool_run_compaction(self, table_location: str, target_file_size_mb: float = 128) -> dict:
        from monitor.recommendations import Recommendation, Urgency
        rec = Recommendation(
            recommendation_type=RecommendationType.COMPACTION,
            urgency=Urgency.HIGH,
            reason="agent-initiated",
            estimated_benefit="",
            action_params={"target_file_size_mb": target_file_size_mb},
        )
        actions = self.executor.execute(table_location, [rec])
        self._actions_taken.setdefault(table_location, []).extend(actions)
        return {"status": "ok", "actions": actions}

    def _tool_expire_snapshots(
        self,
        table_location: str,
        retain_last: int = 10,
        older_than_hours: float = 168,
    ) -> dict:
        from monitor.recommendations import Recommendation, Urgency
        rec = Recommendation(
            recommendation_type=RecommendationType.SNAPSHOT_EXPIRATION,
            urgency=Urgency.MEDIUM,
            reason="agent-initiated",
            estimated_benefit="",
            action_params={
                "retain_last": retain_last,
                "max_snapshot_age_ms": int(older_than_hours * 3600 * 1000),
            },
        )
        actions = self.executor.execute(table_location, [rec])
        self._actions_taken.setdefault(table_location, []).extend(actions)
        return {"status": "ok", "actions": actions}

    def _tool_delete_orphan_files(
        self,
        table_location: str,
        older_than_hours: float = 72,
    ) -> dict:
        from monitor.recommendations import Recommendation, Urgency
        rec = Recommendation(
            recommendation_type=RecommendationType.ORPHAN_CLEANUP,
            urgency=Urgency.LOW,
            reason="agent-initiated",
            estimated_benefit="",
            action_params={"older_than_ms": int(older_than_hours * 3600 * 1000)},
        )
        actions = self.executor.execute(table_location, [rec])
        self._actions_taken.setdefault(table_location, []).extend(actions)
        return {"status": "ok", "actions": actions}

    def _tool_generate_report(self, table_locations: list[str]) -> dict:
        reports = []
        for loc in table_locations:
            metrics = self._metrics_cache.get(loc)
            if metrics is None:
                continue
            recs = self.rec_engine.generate_recommendations(metrics)
            report = self.reporter.generate_report(metrics, recs)
            report.actions_taken = self._actions_taken.get(loc, [])
            reports.append(report)

        summary = self.reporter.generate_summary_report(reports)
        individual = {r.table_location: r.to_text() for r in reports}
        return {"summary": summary, "tables": individual}
