# Lab 8 — Observability and rollback

A pipeline that's deployed but not observable is a pipeline that's
guaranteed to misbehave silently. A pipeline that can't be rolled back is
one whose first failure becomes a long incident.

This lab wires:

1. **Structured logging** conventions for Glue / Step Functions / MWAA /
   AgentCore that play with CloudWatch Logs Insights.
2. **A `deploy_id` correlation key** that links every log line, every
   metric, and every emitted event to the deploy that produced it.
3. **Deploy-tied SLOs** — a simple "did this deploy regress data
   quality?" alarm wired to a deployed-but-rollbackable git ref.
4. **Automated rollback** — `terraform apply` of the previous git SHA on
   alarm breach.
5. **Incident replay via Iceberg time travel** — the recipe for
   answering "what did the curated table look like before the breaking
   deploy?"

## 1. Structured logging conventions

Every log line is a JSON object with at minimum:

```json
{
  "timestamp": "2026-05-29T14:32:08Z",
  "level": "INFO",
  "service": "curate-orders",
  "deploy_id": "v1.4.2",
  "trace_id": "exec-abcdef0123456789",
  "message": "wrote 12345 rows to region_revenue"
}
```

[`logging.py`](logging.py) provides a `get_logger()` helper for the Glue
jobs. It pulls `deploy_id` from a Glue job argument
(`--deploy_id $DEPLOY_ID`) and `trace_id` from the Step Functions
execution name. Wire it via:

```python
from logging_helper import get_logger
log = get_logger(__name__, args)
log.info("wrote rows", extra={"row_count": 12345})
```

## 2. The `deploy_id` correlation key

The CI workflow passes `--deploy_id ${{ github.sha }}` (or the tag, for
prod) to every Glue job and AgentCore invocation. Three downstream
benefits:

- **CloudWatch Logs Insights** queries on `deploy_id` show all logs from
  one deploy across all services.
- **CloudWatch metric dimensions** — emit a custom metric per deploy
  with `deploy_id` as a dimension; alarms key on that.
- **Bedrock invocation audit trail** — `02_security/bedrock-audit-trail/`
  joins on `deploy_id` so you can tell "which deploy is generating these
  expensive prompts."

[`metrics.py`](metrics.py) wraps boto3 CloudWatch with a `put_metric()`
helper that always attaches `deploy_id`.

## 3. Deploy-tied SLOs

The simplest useful SLO for a data pipeline:

> **"For each deploy, the next 24 hours of curated table writes pass
> the DQ contract."**

That's a CloudWatch metric: count of DQ-passed Glue runs vs DQ-failed
Glue runs, dimensioned by `deploy_id`. The alarm:

- Threshold: any DQ failure on a deploy → page.
- Or, more lenient: ≥3 failures in 1 hour → page.

[`alarms.tf`](alarms.tf) wires it. The `treat_missing_data` is set to
`notBreaching` so a sleeping pipeline doesn't false-page.

## 4. Automated rollback

Rollback is just another deploy: `terraform apply` of the previous git
SHA, against the same environment. Two scripts:

- [`scripts/rollback-to-previous-tag.sh`](scripts/rollback-to-previous-tag.sh)
  finds the previous successful release tag, checks it out, runs
  `terraform apply`, and reports.
- [`scripts/rollback-on-alarm.py`](scripts/rollback-on-alarm.py) is
  triggered by SNS from the alarm; it invokes the script above via
  GitHub Actions `workflow_dispatch`.

What rolls back cleanly:

- Glue job script versions (Terraform's `aws_s3_object` reverts)
- Step Functions ASL (Terraform updates the state machine in place)
- IAM policies (Terraform updates the policy doc)
- AgentCore agent versions (the Terraform points at a previous tag's
  prompt files)

What does NOT roll back automatically:

- **Schema migrations.** An Iceberg `ALTER TABLE` that ran on the
  forward deploy is not undone by reverting the IaC. The migration
  needs an explicit "undo" SQL stored alongside it. The lab includes
  [`schema-migrations/README.md`](schema-migrations/README.md) for the
  pattern.
- **Data already written under the new format.** If the new code wrote
  rows in a new schema, the old code won't be able to read them. Use
  Iceberg branches (next section) to protect against this.

## 5. Incident replay via Iceberg time travel

The whole point of Iceberg in the curated zone is that "what did the
table look like before the bad deploy?" is a SQL query, not an archeology
project.

```sql
-- Query the table as it was at deploy v1.4.1 (replace with your own
-- snapshot ID or timestamp).
SELECT *
  FROM region_revenue FOR VERSION AS OF 7382932948293
 WHERE region = 'EU';

-- Or by timestamp:
SELECT *
  FROM region_revenue FOR TIMESTAMP AS OF TIMESTAMP '2026-05-29 14:00:00';
```

[`scripts/snapshot-on-deploy.py`](scripts/snapshot-on-deploy.py) tags the
current Iceberg snapshot with the deploy ID after a successful apply,
so an incident triage can run `FOR VERSION AS OF <deploy_id_snapshot>`
without hunting for the right ID.

The pattern is the same one in
[`../../03_datalake/iceberg-time-travel-debugger/`](../../03_datalake/iceberg-time-travel-debugger/);
this lab links the deploy ID to the snapshot ID so it's findable.

## What "done" looks like

- `aws logs start-query --query-string 'fields @timestamp, message |
  filter deploy_id = "v1.4.2"'` returns all logs from one deploy across
  Glue, Step Functions, and AgentCore.
- A DQ failure on a recent deploy fires an alarm, which triggers an SNS
  message, which kicks off a rollback workflow.
- `SELECT * FROM region_revenue FOR VERSION AS OF '<previous deploy's
  snapshot>'` returns the table as it existed before the breaking
  deploy.

## Common pitfalls

- **Logging without `deploy_id`.** Without the correlation key,
  CloudWatch logs are a chronological soup. Standardise on it from day
  one.
- **Rollback that requires data manipulation.** Rolling back code is
  fine; rolling back data because the new code wrote it is hard.
  Forward-only schema migrations and feature flags on writes (gated by
  deploy ID) are how you avoid this.
- **Pages on missing data.** A pipeline that only runs hourly will
  trigger "no metric data" alarms in the gaps. Always set
  `treat_missing_data = notBreaching` for data-pipeline alarms.
- **No "rollback rehearsal".** If you've never rolled back in staging,
  you'll discover the broken bits during your first prod incident.
  Schedule a quarterly drill that rolls back staging to N-1 and
  forward.
