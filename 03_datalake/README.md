# 03 — Data lake on S3 + Iceberg

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Data lake examples using AWS S3, Glue, Athena, and generative AI.

## Projects

| Project | Description |
|---|---|
| [data-catalog](data-catalog/) | Time-partitioned Bitcoin price ingestion with Glue crawler, Bedrock schema annotation, drift alerting, and governance tagging |
| [datalake-maturity-assessment](datalake-maturity-assessment/) | Read-only scanner that scores an AWS data lake against eight maturity dimensions and emits an evidence-backed report + prioritized remediation backlog mapped to book chapters |
| [data-ingestion-to-s3](data-ingestion-to-s3/) | Simple Lambda that fetches Bitcoin price JSON and writes it to S3 |
| [iceberg-maintenance-agent](iceberg-maintenance-agent/) | Automated Iceberg table maintenance agent |
| [iceberg-time-travel-debugger](iceberg-time-travel-debugger/) | CLI that diffs two Iceberg snapshots and produces a row-level changelog (ADDED/REMOVED rows) using Athena `FOR VERSION AS OF` time-travel syntax |
| [progressive-medallion-starter-kit](progressive-medallion-starter-kit/) | One-command CDK deploy of the full 7-stage progressive medallion architecture: 7 S3 buckets, 5 Glue databases, Lake Formation LF-Tags, Glue crawlers, CDC ingest pipeline, Athena workgroup |
| [lakeformation-data-filters](lakeformation-data-filters/) | Lake Formation column-level and row-level security via data filters: two analyst personas each see a restricted view of the same IMDB principals table |
| [schema-evolution-simulator](schema-evolution-simulator/) | Chaos-engineering tool that runs 12 safe/unsafe/chaos Iceberg schema mutations against a live Athena table and validates downstream consumer behavior |

## Default region

All projects default to **us-east-1**; every deploy script and CLI
honors `AWS_REGION=…` to override.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| progressive-medallion-starter-kit | 8 S3 buckets, 5 Glue databases | $0.0000 | $0.0010 | Pay-per-GB and per-request. |
| progressive-medallion-starter-kit | Glue crawlers (5) | $0.0000 | $0.0010 | $0.44/DPU-hr only while crawling. |
| progressive-medallion-starter-kit | Lambda CDC ingest (15-min schedule) | $0.0000 | $0.0010 | Pay-per-invoke. |
| progressive-medallion-starter-kit | Athena workgroup + SNS | $0.0000 | $0.0001 | $5/TB scanned; SNS first 1k publishes free. |
| data-ingestion-to-s3 | Lambda + S3 | $0.0000 | $0.0001 | No IaC — runs as a single function. |
| data-catalog | S3 + Glue catalog + Lambda + EventBridge | $0.0000 | $0.0010 | Hourly EventBridge rule → Lambda → S3. |
| data-catalog | Bedrock annotation calls | $0.0000 | $0.0020 | Claude Sonnet on schema-change events. |
| iceberg-maintenance-agent | (none) | $0.0000 | $0.0001 | CLI only — operates on existing Iceberg tables. |
| iceberg-time-travel-debugger | S3 + Glue + Athena workgroup + Lambda | $0.0000 | $0.0010 | Athena $5/TB scanned; storage negligible. |
| schema-evolution-simulator | S3 + Glue + Athena workgroup + Lambda | $0.0000 | $0.0010 | Athena $5/TB scanned; storage negligible. |
| lakeformation-bootstrap | S3 + Glue + LF tags/grants + Lambda seed | $0.0000 | $0.0005 | LF tags and grants are free. |
| lakeformation-data-filters | S3 + Glue + Glue crawler + LF filters + Lambda | $0.0000 | $0.0005 | LF data-cell filters free; crawler bills only when run. |
| datalake-maturity-assessment | (none) | $0.0000 | $0.0001 | Read-only scanner — no resources deployed. |
| **Chapter total** | | **$0.0000** | **~$0.0094** | All projects pay-per-use; ~$0/hr at rest. |

This chapter has **zero standing hourly cost** when idle. The
medallion starter kit's 8 buckets and 5 Glue databases bill on storage
and request volume only — leaving a quiet stack deployed costs cents per
month.

