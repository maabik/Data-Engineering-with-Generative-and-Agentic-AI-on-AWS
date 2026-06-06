# 09 — Streaming with Bedrock in the loop

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Two streaming demos that put a foundation model on the hot path:
Kinesis-backed stock trades landing in S3 Tables (Iceberg) with
Bedrock-driven trend analysis, and a social-media sentiment pipeline
with both a Lambda and a Managed Service for Apache Flink
implementation.

## Default region

Both projects default to **us-east-1**. The `deploy-complete-solution.sh`
script and the CDK app accept `AWS_REGION=…` to override.

## Projects

| Project | What it deploys |
|---|---|
| [`stock-trading-demo/`](stock-trading-demo/) | Trade generator → Kinesis → Firehose → S3 Tables (Iceberg) with UPSERTs, plus a trend-analysis Lambda that calls Bedrock Nova Micro and posts SNS alerts. |
| [`streaming-sentiment-demo/`](streaming-sentiment-demo/) | Posts → Kinesis → processor + sentiment analysis (Claude 3.5 Haiku on Bedrock) → S3, with two implementations: simple Lambda pipeline and a Managed Service for Apache Flink job using Async I/O. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| stock-trading-demo | Kinesis Data Stream (2 shards) | $0.0300 | $0.0300 | $0.015/shard-hr provisioned. |
| stock-trading-demo | S3 Tables (Iceberg bucket) | $0.0250 | $0.0250 | Per-active-table base + $0.025/GB-mo storage. |
| stock-trading-demo | Kinesis Data Firehose | $0.0000 | $0.0050 | $0.029/GB ingested; volume-based only. |
| stock-trading-demo | Lambda × 2 + SNS + SQS DLQ + CloudWatch | $0.0000 | $0.0050 | All pay-per-use. |
| stock-trading-demo | Bedrock Nova Micro (trend analysis) | $0.0000 | $0.0050 | $0.000035/1k input + $0.00014/1k output tokens. |
| streaming-sentiment-demo (Lambda) | Kinesis Data Stream (1 shard) | $0.0150 | $0.0150 | $0.015/shard-hr. |
| streaming-sentiment-demo (Lambda) | Lambda × 2 + S3 + SQS DLQ + CloudWatch | $0.0000 | $0.0050 | Pay-per-use. |
| streaming-sentiment-demo (Lambda) | Bedrock Claude Haiku (sentiment) | $0.0000 | $0.0500 | $0.0008/1k input + $0.004/1k output tokens; assumes ~10 posts/min. |
| streaming-sentiment-demo (+ Flink, optional) | Managed Service for Apache Flink (1 KPU) | $0.1100 | $0.1100 | $0.11/KPU-hr; deploy only with `DEPLOY_FLINK=1`. |
| **Chapter total (Lambda only)** | | **$0.0700** | **$0.1400** | Two Kinesis streams + S3 Tables base. |
| **Chapter total (with Flink)** | | **$0.1800** | **$0.2500** | Add 1 KPU. |

The chapter's standing cost comes almost entirely from **provisioned
Kinesis shards** (3 total at $0.015/hr each = $0.045/hr) and the
**S3 Tables active-bucket base** ($0.025/hr). Tear down the streams
and S3 Tables bucket between demo sessions:

```bash
cd 09_streaming/stock-trading-demo && bash teardown.sh
cd 09_streaming/streaming-sentiment-demo && bash teardown.sh
```

The Flink stack is opt-in; skip it (or destroy it) to avoid the
additional $0.11/hr KPU charge.
