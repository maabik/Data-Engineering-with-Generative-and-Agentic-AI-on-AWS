# 11 — QuickSight Q natural-language BI

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Sets up Amazon QuickSight Q on top of the chapter-10 Redshift sales
cluster: data source, SPICE dataset with calculated fields, Q topic
(semantic layer), IAM/networking glue.

## Default region

Defaults to **us-east-1**. The deploy scripts honor `AWS_REGION=…`,
but QuickSight Q topics must live in the same region as their
underlying data source — keep this chapter's region in sync with
`10_datamart/redshift_demo/config.yaml`.

## Project

| Project | What it deploys |
|---|---|
| [`quicksight-q-demo/`](quicksight-q-demo/) | QuickSight → Redshift connection, SPICE-backed dataset, Q topic with semantic types and default aggregations, least-privilege IAM and security-group rules. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| quicksight-q-demo | QuickSight Enterprise (1 author w/ Q) | $0.0329 | $0.0329 | $24/user/month for Author + $250/month per Q-enabled author. Roughly $24/mo ≈ $0.033/hr at the lowest tier; Q add-on is monthly, not hourly. |
| quicksight-q-demo | SPICE storage | $0.0000 | $0.0003 | $0.25/GB-month after 10 GB free. |
| quicksight-q-demo | Secrets Manager | $0.0006 | $0.0006 | $0.40/secret/month for the Redshift password. |
| quicksight-q-demo | (Redshift cluster) | — | — | **Not deployed by this chapter** — see chapter 10 for the $6.52/hr cluster cost. |
| **Chapter total** | | **$0.0335** | **$0.0338** | QuickSight subscription dominates; everything else negligible. |

QuickSight bills **per user per month** rather than per hour, but for
an apples-to-apples table the $24/month Author seat is shown as
$0.033/hr. The Q natural-language add-on is an additional monthly
charge that scales with author count — see the AWS pricing page for
current rates.

This chapter sits on top of chapter 10's Redshift cluster; the cluster
remains the dominant cost in the combined stack at **$6.55/hr fixed
when both are deployed**.
