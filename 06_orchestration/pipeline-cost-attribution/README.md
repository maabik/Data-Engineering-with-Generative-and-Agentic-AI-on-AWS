# pipeline-cost-attribution

Chargeback for shared-services data platforms. Two Lambdas plus a Glue/Athena
layer turn raw Cost Explorer data into a per-pipeline, per-team, per-BU
spend view.

```
            ┌────────────────────────┐
 EventBridge│ aws.glue.CreateJob     │      ┌─────────────────┐
 (CT events)│ aws.states.CreateSM    ├─────►│ tag-enforcer    │
            │ aws.athena.CreateWG    │      │ (Lambda)        │
            └────────────────────────┘      └────────┬────────┘
                                                     │ Tag*Resource
                                                     ▼
                                       ┌──────────────────────────┐
                                       │ Glue jobs / SFN / Athena │
                                       │ tagged with team / BU /  │
                                       │ pipeline_id / env        │
                                       └──────────────────────────┘

 EventBridge   ┌──────────────────┐    ┌──────────────────┐    ┌────────────┐
 rate(1 day)──►│ cost-aggregator  │───►│ s3://.../cost/   │───►│ Glue table │
               │ (Lambda)         │ CE │ ingest_date=YYYY │    │ + Athena   │
               └──────────────────┘    └──────────────────┘    └────────────┘
```

## Why

In PE-owned shared-services orgs, a single platform team operates Glue, Step
Functions, Athena, EMR, and Bedrock for many tenant teams. Finance wants to
charge each business unit for what they used. AWS Cost Explorer can group by
tag — but only if the underlying resources are *consistently tagged*. This
project closes both halves:

1. **tag-enforcer** Lambda reacts to CloudTrail `Create*` events, looks up the
   pipeline → team / BU mapping in S3, and applies tags via the resource's
   service API (Glue `TagResource`, Step Functions `TagResource`,
   Athena `TagResource`).
2. **cost-aggregator** Lambda runs daily, calls `ce:GetCostAndUsage` grouped by
   `tag:team`, `tag:business_unit`, `tag:pipeline_id` × `SERVICE`, unions the
   three slices, and writes JSONL to S3 partitioned by `ingest_date`.
3. **Glue + Athena** expose the data as a partition-projected table backing
   four chargeback queries:
   - `cost_by_team.sql` — daily team spend per service, last 30 days
   - `cost_by_pipeline.sql` — per-pipeline breakdown with first/last seen
   - `chargeback_by_bu.sql` — last-full-month BU rollup (the invoice view)
   - `untagged_spend.sql` — gaps in the chargeback model

## Tag schema

| Key             | Example          | Notes                                  |
| --------------- | ---------------- | -------------------------------------- |
| `team`          | `data-quality`   | Engineering team that owns the pipeline |
| `business_unit` | `platform`       | Cost-center / BU paying the bill       |
| `pipeline_id`   | `dq-demo`        | Stable id for the logical pipeline     |
| `env`           | `dev` / `prod`   | Stage                                  |

Mapping is sourced from `s3://${BUCKET}/config/mapping.json`. Each rule is a
glob `match` plus a tag bag. `sample-mapping.json` is uploaded on first deploy
and is the seed you edit going forward.

## Deploy / test / teardown

```bash
./deploy.sh                                              # bucket, IAM, Lambdas, EventBridge, Glue, Athena
aws lambda invoke --function-name pipeline-cost-cost-aggregator \
  --region us-west-2 /tmp/agg.json && cat /tmp/agg.json  # backfill on demand
QID=$(aws athena start-query-execution \
  --work-group pipeline-cost-wg --region us-west-2 \
  --query-string "$(cat queries/cost_by_team.sql)" \
  --query QueryExecutionId --output text)
aws athena get-query-results --query-execution-id "$QID" --region us-west-2
./teardown.sh                                            # remove everything
```

Override defaults via env: `AWS_REGION`, `PREFIX`, `BUCKET`, `GLUE_DB`.

## Live test result (2026-05-18)

- Aggregator pulled **3,768 rows** of daily-by-tag-by-service CE data into
  `s3://pipeline-cost-${ACCT}-${REGION}/cost/ingest_date=2026-05-18/`.
- `cost_by_team.sql` returned ranked spend, surfacing `untagged` SageMaker
  charges of **$198.72/day** — exactly the kind of shared-services leakage
  this project is meant to catch.
- Creating a state machine named `billing-test-sm` triggered the tag-enforcer
  Lambda within seconds; `aws stepfunctions list-tags-for-resource` confirmed
  the resource was tagged `team=finance, business_unit=finance,
  pipeline_id=billing-etl, env=prod` per the `billing-*` rule in
  `sample-mapping.json`.

## Caveats

- **CE billing data lags ~24h.** The first scheduled run of the aggregator
  produces rows, but most-recent days will be sparse until CE publishes them.
- **CE GroupBy caps at 2 dimensions.** We make 3 calls (one per tag key),
  each grouped with `SERVICE`, and stamp `dimension` / `dimension_value` on
  the rows so a single Athena table covers all three views.
- **Activated cost-allocation tags.** `tag:team`, `tag:business_unit`, etc.
  must be activated in Billing → Cost allocation tags before CE will return
  per-tag groups. New tags can take ~24h to propagate.
- **Tag-enforcer relies on CloudTrail.** Events flow through CloudTrail, so a
  CloudTrail trail (the org's default `management-events` trail counts) must
  be active in the region.

## Files

- `tag_enforcer.py` — Lambda; EventBridge-driven tag applier with manual
  `manual_tag(resource_type, name_or_arn)` entry point for backfills.
- `cost_aggregator.py` — Lambda; daily CE pull → S3 JSONL.
- `sample-mapping.json` — pipeline → tags rule set; seeded on first deploy.
- `queries/` — Athena SQL for the four chargeback views.
- `deploy.sh` / `teardown.sh` — idempotent infra scripts.
