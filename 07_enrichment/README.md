# 07 — Multimodal enrichment and IDP

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Side-by-side comparisons of AWS purpose-built AI services
(Rekognition, Textract) versus a multimodal foundation model (Claude
Sonnet on Bedrock), plus a curated library of Amazon Bedrock Data
Automation custom blueprints with a labelled corpus and a diff-based
test harness.

## Default region

All projects default to **us-east-1**. The notebooks accept a `region`
parameter you can change in the first cell; the BDA scripts honor
`AWS_REGION=…` to override.

## Projects

| Project | What it deploys |
|---|---|
| [`multimodal-enrichment/`](multimodal-enrichment/) | Two notebooks (image enrichment, IDP) that run Rekognition / Textract and Claude on the same inputs and print latency/output. |
| [`bda-blueprint-library/`](bda-blueprint-library/) | Six Bedrock Data Automation blueprints (invoice, bill of lading, K-1, lease abstract, MSA, PO) with labelled corpus, idempotent BDA deploy, and a test harness. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| multimodal-enrichment | (none deployed) | $0.0000 | $0.0000 | Notebooks only. |
| multimodal-enrichment | Rekognition / Textract / Bedrock | $0.0000 | $0.0500 | Pay-per-call only — varies wildly with image / page count. |
| bda-blueprint-library | S3 bucket | $0.0000 | $0.0001 | Pay-per-storage (negligible at sample-corpus scale). |
| bda-blueprint-library | BDA blueprints (6 deployed) | $0.0000 | $0.0000 | Blueprint definitions are free; only invocations bill. |
| bda-blueprint-library | BDA per-page processing | $0.0000 | $0.0400 | Custom output is $0.04/page on the standard tier. |
| **Chapter total** | | **$0.0000** | **~$0.0900** | All pay-per-use; idle cost is essentially zero. |

This chapter has **zero standing hourly cost** when idle — Rekognition,
Textract, Bedrock, and BDA are 100% pay-per-call. The active numbers
above assume one IDP demo run per hour with a small document corpus.
