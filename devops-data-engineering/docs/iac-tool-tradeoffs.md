# IaC tool tradeoffs for data engineering

This chapter codebase is Terraform-primary. The CDK appears alongside the
same pipeline so you can compare. CloudFormation gets a brief mention.

The point of this doc isn't "tool X wins". It's that **data work has
different ergonomics than stateless web work**, and that asymmetry should
inform which IaC tool you reach for.

## What's different about data IaC

Three properties of data infrastructure push back on the usual "any IaC tool
will do":

1. **State lives outside the resource.** A Glue job's *config* is in IaC,
   but its *script* is in S3, its *catalog tables* are managed by the
   running job, and its *bookmarks* sit in the Glue service. Replacing the
   resource doesn't replace the state — and recreating it can lose progress.
2. **Resources span dialects.** A pipeline includes IAM, S3, Glue, Step
   Functions ASL, Athena workgroups, MWAA Airflow DAGs, AgentCore runtimes,
   sometimes Redshift / Lake Formation grants. Each is a different
   abstraction with different drift patterns.
3. **Some resources have minute-level deploy times.** Redshift cluster
   modifications, MWAA upgrades, AgentCore deploys — these are not
   "ten-second `apply` and you're done." Plan output that says
   "1 to change" can mean a 20-minute reboot.

A good IaC choice for data work is one that handles those three properties
without forcing you into 90% workarounds.

## Terraform — what it's good at, what hurts

**Good:**

- **First-class state model.** `terraform state` lets you import existing
  resources, move them between modules, and rename without recreating —
  essential when the underlying resource is stateful (an Iceberg table, a
  Redshift cluster, a Lake Formation grant).
- **Provider coverage and predictability.** The `aws` provider is mature.
  When AWS adds a new resource type, it shows up as `aws_*` within weeks
  with documented arguments — important for AgentCore, S3 Vectors, and
  other newer services.
- **Plan output that humans can review.** A `terraform plan` is reviewable
  in a PR. Reviewers can see "this will replace the Glue job" before it
  happens.
- **Workspaces map cleanly to ephemeral sandboxes.** See lab 3.

**Hurts:**

- HCL is data, not code. You can't easily express "for each table in this
  list, create a Glue job, an Athena view, and a Lake Formation grant" —
  you can do it with `for_each`, but it's awkward past two levels of
  iteration.
- IAM policies in HCL are verbose. The CDK's `iam.PolicyStatement` API is
  ergonomic; the Terraform `aws_iam_policy_document` data source is
  serviceable but noisy.
- Cross-stack references via `terraform_remote_state` are fragile. Splitting
  a pipeline into "networking stack" + "pipeline stack" + "observability
  stack" works, but the seams are explicit and have to be maintained.

## CDK — what it's good at, what hurts

**Good:**

- **Real code.** Loops, conditionals, helper functions. If your pipeline has
  N tables and each needs the same scaffolding, CDK expresses that in 20
  lines of TypeScript that would be 200 lines of HCL.
- **L2 constructs hide grunt work.** `s3.Bucket(this, 'raw',
  {encryption: BucketEncryption.KMS_MANAGED, ...})` is shorter and harder
  to misconfigure than the equivalent Terraform.
- **Type checking.** A TypeScript compiler catches a misnamed property
  before plan time.

**Hurts:**

- **Synthesises to CloudFormation.** Inherits CFN's limits: stack resource
  limits, slow updates, "UPDATE_ROLLBACK_FAILED" recovery is harder than
  `terraform apply` after fixing an error.
- **State is in CloudFormation drift detection**, which is less mature than
  `terraform plan`. Drift on Glue and Lake Formation is especially weak.
- **Refactors change logical IDs by default.** Move a construct between
  stacks and CFN sees a delete + create — fatal for stateful resources
  unless you carefully use `overrideLogicalId` or `applyRemovalPolicy`.
- **The diff is a CloudFormation change set.** Reviewable, but more verbose
  and less direct than `terraform plan`'s "1 to add, 0 to change, 1 to
  destroy."

## CloudFormation (raw YAML) — when it makes sense

Rarely the right primary choice for a data pipeline, but it has uses:

- **Quick-start templates** that ship with AWS docs (the load balancer in
  `02_security/tls-load-balancer/` is one). Easier to point at than a
  Terraform module if you're trying to teach AWS, not Terraform.
- **AWS Service Catalog products.** SC speaks CFN natively.
- **You're inheriting one.** Fine — keep it, wrap the next thing in
  Terraform, and accept the seam.

What it's bad at: anything dynamic, anything that needs `for_each`-style
iteration, anything where you'd want to compute a value from a list of
inputs. Yes, you can do macros and transforms — and you'll regret it.

## How to choose, in one paragraph

If your team writes mostly Python or TypeScript and the pipeline has a
**lot of repeated structure** (50 Glue jobs, 100 Athena views), CDK pays
back its weight. If your team has a mix of languages, deploys against a
**lot of mature AWS services** that need predictable plan/apply behavior,
and especially if **drift detection on stateful resources matters**, pick
Terraform. If you're already shipping the rest of your platform in one of
them, use that one — the cost of a second IaC tool in the same team is
higher than its benefits in almost every case.

## Why this chapter codebase is Terraform-primary

For the reference pipeline:

- `terraform plan` against a real AWS account gives a clean, reviewable
  diff including Glue job script versions and IAM policies.
- Workspaces (lab 3) collapse the "per-developer sandbox" problem to one
  config flag.
- The seam between "Terraform-managed resource" and "data managed by the
  resource" (Iceberg tables, Glue catalog updates from running jobs) is
  better-documented in the Terraform community than the CDK community.

CDK is in [`reference-pipeline/cdk/`](../reference-pipeline/cdk/) so the
shape is visible. The labs reference the Terraform paths; CDK users can
translate trivially.
