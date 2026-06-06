# Operating costs

These are **measured** numbers from the PE due-diligence sample
workload (eight markdown documents, ~5KB total). They are not vendor
estimates. Re-measure for your own corpus before you commit to a
budget — the costs scale roughly linearly with document count and
sublinearly with question count.

## Sample-workload measurements

| Component                   | Unit cost (us-east-1) | Sample workload usage         | Sample cost |
| --------------------------- | --------------------- | ----------------------------- | ----------- |
| Claude Sonnet 4.5 (extract) | $3.00 / 1M input, $15 / 1M output | 18,200 input + 4,400 output tokens | $0.12 |
| Nova 2 Multimodal Embeddings | $0.0001 / 1K tokens   | ~5,800 embedding tokens       | $0.0006     |
| Neptune Analytics           | $0.20 / m-NCU-hour    | 128 m-NCU × 0.1 h ingest       | $2.56       |
| S3 Vectors                  | $0.06 / 1M vectors stored / month + $0.001 per 1K queries | 142 vectors stored, 32 queries | <$0.01 |
| AgentCore Runtime           | $0.025 / hour idle, $0.10 / 1000 invocations | 0.05 h running, 12 invocations | $0.001 |
| Step Functions (Standard)   | $0.025 / 1K state transitions | ~16 transitions               | <$0.01      |
| **Sample workload total**   |                       |                               | **~$2.70**  |

Most of that is the Neptune Analytics provisioned capacity, which is
hourly regardless of utilization. For an always-on graph, expect
**~$144/month** at the smallest 128 m-NCU size.

## Per-query cost (steady-state)

After ingest, each agent question costs:

| Phase                      | Tokens / units    | Cost per query |
| -------------------------- | ----------------- | -------------- |
| Embedding (1× Nova call)   | ~50 input tokens  | <$0.0001       |
| S3 Vectors top-K query     | 1 query           | <$0.0001       |
| Neptune 1-hop × 20 entities| 20 queries        | included in capacity |
| Claude reasoning (Sonnet)  | ~3,000 input + ~600 output tokens | $0.018 |
| **Per-query total**        |                   | **~$0.019**    |

So a 100-question session costs about $1.90 in marginal Bedrock spend
on top of the always-on Neptune capacity.

## Cost knobs

- **Reduce extraction model.** Falling back to Claude Haiku for entity
  extraction (keeping Sonnet for relationships) cuts ingest token spend
  by ~70% on the sample workload. Acceptable for many domains.
- **Drop traversal depth.** Default 2-hop traversal is fine for PE DD;
  for simple FAQ workloads, depth=1 reduces graph load by ~5×.
- **Larger batch in ingest.** Ingest is bursty — running it nightly with
  Neptune at 128 m-NCU and torn down between runs saves ~80% of the
  capacity bill if you only ingest once a day.

## What we did *not* measure

- KMS key cost ($1/month per key, fixed)
- CloudWatch log retention ($0.50/GB ingested + $0.03/GB stored)
- Data transfer out (negligible at this scale)

These each add tens of cents per month at the sample scale and are
omitted from the table to avoid false precision.

## How to reproduce these measurements

```bash
# After a clean ingest, query CloudWatch for token usage:
aws cloudwatch get-metric-statistics \
  --namespace AWS/Bedrock \
  --metric-name InputTokenCount \
  --start-time $(date -u -v-1H +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 3600 \
  --statistics Sum

# Neptune capacity is in the bill, not metrics — use Cost Explorer with
# filters: Service=Amazon Neptune, UsageType=*NeptuneAnalytics*.
```
