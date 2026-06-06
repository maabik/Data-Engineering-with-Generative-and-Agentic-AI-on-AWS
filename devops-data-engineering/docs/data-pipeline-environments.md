# Environments for stateful data pipelines

The DevOps "dev / staging / prod" pattern is borrowed from web services and
fits them well: stateless apps, short feedback cycles, easy to throw away
and rebuild. Data pipelines violate every one of those assumptions. The
environment model has to be tuned for that, or you end up with one of two
failure modes:

- **One real environment, many fake ones.** "We have dev, staging, and
  prod" — but only prod has real data, only prod talks to the real upstream
  API, only prod's IAM looks anything like production. Bugs surface only in
  prod. This is the default trajectory.
- **Three full copies of prod.** Easy to reason about, three times the
  AWS bill, and engineers still test in prod because the staging data is
  six months stale.

This doc lays out the environments this chapter codebase recommends and the
tradeoffs they make.

## Environments at a glance

| Environment | Lifetime | Data | AWS account | Who owns it |
|---|---|---|---|---|
| **Local dev** | Minutes–hours | Synthetic / fixtures | None (LocalStack / moto for boundary tests) | Each developer |
| **Sandbox** | Hours–days, ephemeral | Real-shape, small-volume from a frozen snapshot | Sandbox account, per-developer or per-PR | Each developer / PR |
| **Staging** | Persistent | Daily-refreshed sample of prod (or full prod if size allows) | Staging account | Platform team |
| **Prod** | Persistent | Production data | Prod account | Platform team |

Four environments, not three. The split between *local dev* and *sandbox*
is what lets you have fast inner-loop testing **and** real-AWS confidence
before merging.

## Local dev

**Goal**: tight inner loop. Edit a Glue job's PySpark, run unit tests,
re-run, all without touching AWS.

**What to mock**:

- The PySpark execution itself: use `pyspark` locally with a tiny
  `SparkSession`. Glue-specific APIs (`GlueContext`, dynamic frames) get
  thin shims — see `reference-pipeline/glue_jobs/` for the pattern.
- Boto3 calls: `moto` for unit tests, `LocalStack` for integration-shape
  tests if you want the full stack on your laptop. Both have caveats —
  Glue, Step Functions, and Lake Formation support varies.

**What you can't mock**:

- IAM. Anything where the bug might be "the role doesn't have the right
  permission" needs a real AWS account. That's what the sandbox is for.
- Service-specific quirks. Glue's commit semantics, Step Functions' I/O
  size limits, AgentCore's invocation model. Mocks don't reproduce these.

**Rule of thumb**: if a test passes locally and fails in sandbox, the bug
was in the mock, not the code. Don't chase it locally — fix the test in
sandbox.

## Sandbox — the most important environment

The sandbox is what makes the rest of the model work. **A sandbox is an
ephemeral, real-AWS environment that's cheap enough to spin up per-PR or
per-developer, scoped to one developer's blast radius, and destroyed when
the work is done.**

This chapter codebase's lab 3 wires this up via Terraform workspaces:

```bash
# Each developer (or each PR) gets their own workspace
export TF_WORKSPACE=alice-feature-x
export TF_VAR_owner=alice
terraform apply

# When done — or when the PR closes — destroy
terraform destroy
```

A few non-obvious things about sandboxes for data pipelines:

- **Real services, scaled down.** A sandbox Redshift cluster is the smallest
  RA3 size. A sandbox Glue job uses 2 DPUs, not 50. A sandbox MWAA
  environment is `mw1.small`. Cost goes from "thousands per month per
  developer" to "tens".
- **Real data, anonymised + sampled.** The sandbox's S3 raw zone is seeded
  from a snapshot of prod that has been PII-stripped and sampled to ~1% of
  rows. Lab 3 includes a `sandbox-seed.py` script that does this.
- **A teardown deadline, enforced.** Stale sandboxes are the #1 source of
  surprise AWS bills. Lab 3 wires up:
  - Auto-teardown on PR close (GitHub Actions workflow).
  - A weekly Lambda that destroys any workspace whose `last_applied_at`
    tag is older than seven days, after Slacking the owner.
  - An AWS Budgets alarm scoped to the owner tag.
- **Resource names include the workspace.** `${workspace}-raw-bucket`,
  `${workspace}-curated-bucket`. Otherwise two developers' workspaces
  collide on bucket names and the second `apply` fails.

## Staging

Staging is the only environment that's allowed to be slow, expensive, and
boring. It exists for one purpose: to **catch the bugs that only show up at
something approaching production data volumes**, on the same instance types
prod uses, with the same IAM.

**Properties of a good staging:**

- One staging environment, not per-developer. It's persistent. Deploys to
  staging happen on every merge to `main`; they queue, they don't fan out.
- Same instance types as prod. Same Redshift node type, same MWAA size,
  same Glue worker class. If you'd never run prod on `mw1.small`, don't
  validate the change on `mw1.small`.
- Data refreshed nightly from a redacted prod snapshot. Lab 3 includes the
  pattern; the production snapshot itself is governed (Lake Formation, KMS,
  audit-logged), since "redacted" is a lossy operation and bugs in the
  redactor leak PII into staging.
- Failures in staging block prod deploys. A green staging deploy is a
  precondition for the prod approval gate in lab 6.

**What staging is not:** a pre-prod testing ground for in-progress work.
That's what sandbox is. By the time something hits staging, it's been
reviewed, sandbox-tested, and merged.

## Prod

The boring one. Once everything else is set up correctly, prod deploys are
mechanical. The interesting parts are:

- **Manual approval gate.** Lab 6 uses GitHub `environments` to require an
  approver before the prod `apply` runs.
- **Concurrency 1.** Two prod deploys can't race. The CI workflow uses
  `concurrency: prod-data-pipeline` so the second deploy waits for the
  first.
- **Deploy-tied SLOs.** Lab 8 wires CloudWatch dashboards keyed off
  `deploy_id`, so when a pipeline run breaks data quality, you can correlate
  the alarm to the deploy that caused it.
- **Rollback is `terraform apply` of the previous git SHA.** Not a "rollback
  button". Treating rollback as just another deploy keeps the path tested.

## Drift between environments

The single rule that holds the whole model together:

> **The same Terraform module deploys to all environments. Differences live
> in `.tfvars`, not in branches or in commented-out blocks.**

If the staging `aws_glue_job` has a `number_of_workers = 2` and prod has
`number_of_workers = 50`, that's two values of one variable, not two
modules. The moment you fork the module per environment, staging stops
catching prod's bugs.

Variables that legitimately differ:

- Sizing (`number_of_workers`, Redshift `node_type`, MWAA `environment_class`).
- Schedule (sandbox cron is "never"; staging is daily; prod is hourly).
- Source data location (S3 prefix per environment).
- Allowed CIDR ranges, KMS key ARNs.

Variables that should *not* differ:

- IAM policies (modulo the bucket name interpolation).
- Glue job script content.
- Step Functions ASL.
- Lake Formation grants — same logical grants, different principal ARNs.

Lab 6 includes a CI check that diffs the rendered Terraform plan between
staging and prod, ignoring known-different inputs, and fails the build if
anything else differs.

## How this maps to the labs

- **Lab 3** builds the sandbox infrastructure (workspaces, owner tags,
  teardown automation, budgets).
- **Lab 5** tells you which tests run in which environment (unit/contract
  in CI on every push; integration in sandbox on every PR; smoke in
  staging post-deploy; canary in prod).
- **Lab 6** wires the environments into the GitHub Actions deployment
  pipeline.
- **Lab 7** scopes the OIDC roles per environment so the staging deploy
  job physically can't touch prod.
- **Lab 8** keys observability and rollback to deploy IDs that span
  environments.
