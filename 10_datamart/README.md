# 10 — Redshift + text-to-SQL

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

A complete star-schema sales mart on Redshift RA3 with a
natural-language query interface: Bedrock TextToSQL agent, Claude
Sonnet fallback, Lambda + API Gateway, and a single-page HTML frontend.

## Default region

The single project here defaults to **us-east-1** (set in
`redshift_demo/config.yaml`). To deploy elsewhere, edit
`config.yaml` (`aws.region`) before running `deploy.sh`. Chapters 11
and 12 expect the cluster to be in the same region they are.

## Project

| Project | What it deploys |
|---|---|
| [`redshift_demo/`](redshift_demo/) | Faker-generated star schema on Redshift, plus a CFN stack that fronts it with a text-to-SQL API and HTML frontend. Used by chapters 11 and 12 as the source warehouse. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| redshift_demo | Redshift `ra3.4xlarge` × 2 nodes | **$6.5200** | **$6.5200** | $3.26/node-hr on-demand — the biggest line in the entire book. |
| redshift_demo | S3 staging bucket | $0.0000 | $0.0001 | Pay-per-storage; small dataset. |
| redshift_demo | Lambda (text-to-SQL) | $0.0000 | $0.0010 | Pay-per-invoke. |
| redshift_demo | API Gateway (REST) | $0.0000 | $0.0010 | $3.50/M calls; demo traffic is negligible. |
| redshift_demo | DynamoDB (prompt templates) | $0.0000 | $0.0001 | On-demand mode. |
| redshift_demo | Bedrock (text-to-SQL agent + Claude fallback) | $0.0000 | $0.0050 | Per-call only. |
| redshift_demo | Secrets Manager | $0.0006 | $0.0006 | $0.40/secret/month. |
| **Chapter total** | | **$6.5206** | **$6.5278** | Cluster dominates everything else by 1000×. |

This chapter has the **single largest standing cost in the book**:
$6.52/hr ≈ $4,700/month for the Redshift cluster alone. **Tear down or
pause the cluster between sessions** — the `redshift_demo/teardown.sh`
script cleans it up, and Redshift pause/resume is also documented in
the project README.

To slow the bleed without a full teardown, switch the cluster to
**Redshift Serverless** in `config.yaml` (RPU-based; can scale to
zero) or downsize to `ra3.large` × 1 ($0.96/hr).
