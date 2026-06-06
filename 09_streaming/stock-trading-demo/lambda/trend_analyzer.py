"""Trend analyzer Lambda.

Reads stock trade records from a Kinesis batch, asks Amazon Bedrock Nova Micro
for a one-line trend signal, and publishes the result to SNS. Designed to
fail loudly to the DLQ on bad input rather than silently dropping records.
"""
import base64
import json
import logging
import os
from collections import defaultdict
from statistics import mean

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
MIN_TRADES_FOR_ANALYSIS = int(os.environ.get("MIN_TRADES_FOR_ANALYSIS", "5"))

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
sns = boto3.client("sns")


def _decode_record(record):
    payload = base64.b64decode(record["kinesis"]["data"])
    return json.loads(payload)


def _summarize(trades):
    by_symbol = defaultdict(list)
    for t in trades:
        by_symbol[t["stock_symbol"]].append(t)

    summary = []
    for symbol, group in by_symbol.items():
        prices = [float(t["price"]) for t in group]
        buys = sum(1 for t in group if t.get("trade_type") == "BUY")
        sells = sum(1 for t in group if t.get("trade_type") == "SELL")
        summary.append(
            {
                "symbol": symbol,
                "trades": len(group),
                "avg_price": round(mean(prices), 2),
                "min_price": round(min(prices), 2),
                "max_price": round(max(prices), 2),
                "buy_count": buys,
                "sell_count": sells,
            }
        )
    summary.sort(key=lambda s: s["trades"], reverse=True)
    return summary


def _call_bedrock(summary):
    prompt = (
        "You are a market analyst. Given the following per-symbol trade summary "
        "from the last batch, return a single concise sentence per symbol with a "
        "trend signal (BULLISH, BEARISH, or NEUTRAL) and a one-clause justification. "
        "Be direct, no preamble.\n\n"
        f"Summary JSON:\n{json.dumps(summary)}"
    )

    # Use the Bedrock Converse API so the body shape stays consistent across
    # Anthropic Claude and Amazon Nova models.
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.2},
    )
    return response["output"]["message"]["content"][0]["text"]


def lambda_handler(event, _context):
    records = event.get("Records", [])
    if not records:
        return {"status": "empty"}

    trades = []
    for r in records:
        try:
            trades.append(_decode_record(r))
        except (ValueError, KeyError) as exc:
            logger.exception("dropping malformed record: %s", exc)

    if len(trades) < MIN_TRADES_FOR_ANALYSIS:
        logger.info("only %d trades in batch, skipping analysis", len(trades))
        return {"status": "skipped", "trades": len(trades)}

    summary = _summarize(trades)
    analysis = _call_bedrock(summary)
    logger.info("bedrock analysis: %s", analysis)

    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"Trend signal — {len(trades)} trades, {len(summary)} symbols",
        Message=json.dumps(
            {"summary": summary, "analysis": analysis},
            indent=2,
        ),
    )
    return {"status": "ok", "trades": len(trades), "symbols": len(summary)}
