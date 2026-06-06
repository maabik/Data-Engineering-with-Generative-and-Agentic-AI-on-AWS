# QuickSight Q Demo Setup

This repository sets up an Amazon QuickSight Q demo on top of a Redshift sales
cluster: a data source, dataset (SPICE), Q topic with semantic layer, and the
IAM/networking glue between them.

## Overview

QuickSight Q is Amazon's natural language query interface. This demo
configures:

- **Data source** — QuickSight -> Redshift connection
- **Dataset** — SPICE-backed dataset with calculated fields
- **Q topic** — semantic layer (column descriptions, semantic types,
  default aggregations) used for natural-language queries
- **IAM + VPC** — least-privilege policy on a QuickSight service role and
  ingress whitelisted to the AWS-published QuickSight CIDR for the configured
  region

## Prerequisites

1. **AWS CLI** configured with credentials that can manage QuickSight, IAM,
   Redshift (incl. Data API), Secrets Manager, and EC2 security groups.
2. **Redshift cluster** with sales data — default ID `sales-demo-cluster`,
   database `salesdb` (override via `config.env`).
3. **QuickSight Enterprise** subscription with a registered user.
4. **Tools on PATH**: `aws`, `jq`, `openssl`.

## Required configuration

Set the two identity variables before running anything. Everything else has
sensible defaults in `config.env` and can be overridden via env vars.

```bash
export QS_USER_ARN='arn:aws:quicksight:us-east-1:123456789012:user/default/alice'
export NOTIFICATION_EMAIL='alice@example.com'
```

`config.env` is sourced by every script — edit it (or override via env) to
change the cluster ID, region, resource IDs, etc. The script never hardcodes
your account ID, cluster DNS, or username.

## Quick start

```bash
# 1. Validate prerequisites (cluster reachable, QuickSight user exists, etc.)
./validate-setup.sh

# 2. Run the orchestrator — it sequences all steps below.
./setup-quicksight-q-demo.sh
```

The orchestrator runs:

1. `bootstrap-secret.sh` — generates a strong random password and stores it
   in Secrets Manager (`QS_DB_SECRET_NAME`, default
   `quicksight-q-demo/quicksight_user`).
2. `setup-quicksight-redshift-permissions.sh` — IAM policy + service role +
   VPC SG ingress for the QuickSight egress CIDR (no `0.0.0.0/0`).
3. `create-quicksight-db-user.sh` — `CREATE USER` (or `ALTER USER`) on
   Redshift via the Data API, password pulled from Secrets Manager.
4. `01-create-datasource.sh` — QuickSight data source pointing at the
   discovered cluster endpoint.
5. `02-create-dataset.sh` — SPICE dataset + ingestion.
6. `03-create-topic.sh` — Q topic (idempotent: update if exists).

After the run, `setup-summary.sh` prints the state of every resource.

## Sample queries

Once setup completes, open the QuickSight console and try:

- "What are our total sales this year?"
- "Which region has the highest sales?"
- "Show me sales by product category"
- "What are our most profitable products?"

See `sample-queries.md` for the full curated list.

## Scripts

| Script | Purpose |
| ------ | ------- |
| `setup-quicksight-q-demo.sh` | Top-level orchestrator |
| `bootstrap-secret.sh` | Create / rotate the DB password in Secrets Manager |
| `setup-quicksight-redshift-permissions.sh` | IAM policy + role + VPC SG ingress |
| `create-quicksight-db-user.sh` | Redshift user + base GRANTs |
| `grant-quicksight-permissions.sh` | Extra GRANTs + comprehensive view (sales schema) |
| `01-create-datasource.sh` | QuickSight data source |
| `02-create-dataset.sh` | QuickSight dataset (SPICE) |
| `03-create-topic.sh` | QuickSight Q topic |
| `setup-summary.sh` | Read-only status snapshot |
| `validate-setup.sh` | Pre-flight prerequisites check |
| `check-redshift-tables.sh` | Read-only Redshift schema inspection |
| `cleanup.sh` | Tear down everything created by the demo |

Shared helpers live in `lib/common.sh`.

## Cleanup

```bash
./cleanup.sh
```

Tears down: dashboard, Q topic, dataset, data source, IAM policy
(`QuickSightRedshiftAccess`), the Redshift user and its views, and the
Secrets Manager secret. Does **not** delete the Redshift cluster or the
QuickSight service role.

## Architecture

```
Redshift cluster ──> QuickSight data source ──> SPICE dataset ──> Q topic
                                                                    │
                                                                    ▼
                                                             Natural language Q
```

Auth: QuickSight authenticates to Redshift as `quicksight_user` with a
password fetched at apply-time from Secrets Manager. No password ever lands
in this repo.
