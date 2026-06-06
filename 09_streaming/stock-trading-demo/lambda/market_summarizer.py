"""
Hourly market-summary Lambda.

Triggered by EventBridge once per hour. Runs three Athena queries against
trading.trades (the same per-symbol / buy-sell-pressure / top-movers analytics
shipped in queries/aggregations.sql), feeds the results to Bedrock Claude Haiku
4.5 for a one-paragraph executive summary, and publishes the summary to SNS.

Environment variables (all required):
  GLUE_DATABASE        — Glue catalog database (e.g. "trading")
  ATHENA_CATALOG       — Athena DataCatalog name fronting the federated
                         s3tablescatalog (e.g. "s3tables_stock_trading")
  ATHENA_OUTPUT_BUCKET — bucket name for query results
  ATHENA_WORKGROUP     — workgroup, defaults to "primary"
  BEDROCK_MODEL_ID     — e.g. "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  SNS_TOPIC_ARN        — topic to publish the summary to
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

GLUE_DATABASE = os.environ["GLUE_DATABASE"]
ATHENA_CATALOG = os.environ["ATHENA_CATALOG"]
ATHENA_OUTPUT_BUCKET = os.environ["ATHENA_OUTPUT_BUCKET"]
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]

athena = boto3.client("athena")
bedrock = boto3.client("bedrock-runtime")
sns = boto3.client("sns")

TOP_VOLUME_SQL = """
SELECT stock_symbol,
       COUNT(*)                AS trade_count,
       ROUND(AVG(price), 2)    AS avg_price,
       ROUND(SUM(total_value), 2) AS notional_volume
FROM {db}.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY notional_volume DESC
LIMIT 5
"""

BUY_SELL_PRESSURE_SQL = """
SELECT stock_symbol,
       SUM(CASE WHEN trade_type = 'BUY'  THEN 1 ELSE 0 END) AS buys,
       SUM(CASE WHEN trade_type = 'SELL' THEN 1 ELSE 0 END) AS sells,
       ROUND(100.0 * SUM(CASE WHEN trade_type = 'BUY' THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0), 1) AS buy_pct
FROM {db}.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY buy_pct DESC
LIMIT 5
"""

TOP_MOVERS_SQL = """
SELECT stock_symbol,
       ROUND(MIN(price), 2) AS low,
       ROUND(MAX(price), 2) AS high,
       ROUND(100.0 * (MAX(price) - MIN(price)) / NULLIF(MIN(price), 0), 2) AS pct_range
FROM {db}.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY pct_range DESC
LIMIT 5
"""


def lambda_handler(event: dict, context: Any) -> dict:
    LOG.info("market_summarizer invoked: %s", json.dumps(event))

    sections = {
        "top_volume": _run_query(TOP_VOLUME_SQL.format(db=GLUE_DATABASE)),
        "buy_sell_pressure": _run_query(BUY_SELL_PRESSURE_SQL.format(db=GLUE_DATABASE)),
        "top_movers": _run_query(TOP_MOVERS_SQL.format(db=GLUE_DATABASE)),
    }

    if not any(sections.values()):
        LOG.warning("no rows returned for any query — skipping summary")
        return {"status": "no_data"}

    summary = _summarize(sections)
    LOG.info("publishing summary: %s", summary)

    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject="Hourly Market Summary",
        Message=summary,
    )
    return {"status": "ok", "summary": summary}


def _run_query(sql: str) -> list[dict]:
    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={
            "Catalog": ATHENA_CATALOG,
            "Database": GLUE_DATABASE,
        },
        ResultConfiguration={
            "OutputLocation": f"s3://{ATHENA_OUTPUT_BUCKET}/market-summary/"
        },
        WorkGroup=ATHENA_WORKGROUP,
    )
    qid = response["QueryExecutionId"]

    deadline = time.time() + 60
    while time.time() < deadline:
        execution = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    else:
        LOG.error("athena query %s timed out", qid)
        return []

    if state != "SUCCEEDED":
        LOG.error(
            "athena query %s ended in state %s: %s",
            qid,
            state,
            execution["Status"].get("StateChangeReason"),
        )
        return []

    results = athena.get_query_results(QueryExecutionId=qid, MaxResults=20)
    rows = results["ResultSet"]["Rows"]
    if not rows:
        return []
    headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
    return [
        dict(zip(headers, [c.get("VarCharValue", "") for c in row["Data"]]))
        for row in rows[1:]
    ]


def _summarize(sections: dict[str, list[dict]]) -> str:
    prompt = (
        "You are a markets analyst writing a one-paragraph hourly summary for "
        "a trading desk. Cover overall activity, the most traded names, "
        "buy/sell skew, and any notable price ranges. Be specific with "
        "symbols and numbers when present. Keep it under 120 words.\n\n"
        f"Top volume (last hour): {json.dumps(sections['top_volume'])}\n"
        f"Buy/sell pressure: {json.dumps(sections['buy_sell_pressure'])}\n"
        f"Top movers: {json.dumps(sections['top_movers'])}\n"
    )
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.3},
    )
    text = (
        response.get("output", {})
        .get("message", {})
        .get("content", [{}])[0]
        .get("text", "")
    ).strip()
    return text or "Hourly summary unavailable: model returned no content."
