# Lab 3 — IaC sandbox environments

If lab 2 is the cheapest DevOps practice, this is the highest-leverage
one. Per-developer (or per-PR) ephemeral AWS sandboxes change the
testing model from "wait for staging to free up" to "test on real AWS in
parallel with everyone else."

This lab wires:

1. **Terraform workspaces** so two developers don't collide on resource
   names.
2. **`TF_VAR_owner`** as the blast-radius tag, propagated to every billable
   resource.
3. **Auto-teardown** on PR close (GitHub Action) and after seven days of
   inactivity (scheduled Lambda).
4. **AWS Budgets** scoped per-owner, alarming before bills get scary.

Cost matters here: the sandbox version of the reference pipeline runs at
roughly a few cents per active day. Forgotten sandboxes are the killer —
which is why teardown is a first-class concern, not an afterthought.

---

## 1. Workspaces

The reference pipeline's `terraform/envs/sandbox/` already supports this.
The convention:

```bash
export TF_WORKSPACE=$USER-feature-x       # or PR-NUMBER
export TF_VAR_owner=$USER                 # required (validated)

cd reference-pipeline/terraform/envs/sandbox
terraform init
terraform workspace new $TF_WORKSPACE 2>/dev/null || terraform workspace select $TF_WORKSPACE
terraform apply
```

Resource names interpolate `terraform.workspace`, so two workspaces in the
same AWS account get two non-colliding sets of buckets, Glue jobs, and
state machines.

**State backend.** All workspaces share one S3 backend bucket; workspaces
keep their state files separate inside it. Bootstrap once with
[`scripts/bootstrap-tf-backend.sh`](../../scripts/bootstrap-tf-backend.sh).

## 2. The `owner` tag

Every billable resource in the reference pipeline carries an `Owner` tag
applied through `provider "aws" { default_tags { ... } }`. Three reasons:

- **Cost attribution.** AWS Cost Explorer can group by tag once activated;
  the budget alarm in step 4 keys off it.
- **Auto-teardown discovery.** The Lambda in step 3 lists resources by
  tag, not by Terraform state, so it can clean up even if state is
  corrupted.
- **Incident triage.** When a sandbox resource shows up in CloudTrail
  doing something unexpected, the tag answers "whose is this?"

The validation in `variables.tf` rejects `terraform apply` without an
owner. Don't remove that.

## 3. Auto-teardown

**Two layers, intentionally:**

### a) On PR close

[`.github/workflows/sandbox-teardown.yml`](.github/workflows/sandbox-teardown.yml)
runs on `pull_request: closed` (merged or not):

```yaml
on:
  pull_request:
    types: [closed]

jobs:
  destroy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.SANDBOX_DEPLOY_ROLE }}
          aws-region: us-east-1
      - run: |
          cd reference-pipeline/terraform/envs/sandbox
          export TF_WORKSPACE=pr-${{ github.event.pull_request.number }}
          export TF_VAR_owner=pr-${{ github.event.pull_request.number }}
          terraform init
          terraform workspace select $TF_WORKSPACE || exit 0
          terraform destroy -auto-approve
          terraform workspace select default
          terraform workspace delete $TF_WORKSPACE
```

The included file is dropped at `.github/workflows/sandbox-teardown.yml`
in your repo root.

### b) Scheduled cleanup of stale sandboxes

PRs sometimes get abandoned. A scheduled cleanup picks up the rest:

- [`scripts/sweep-stale-sandboxes.py`](scripts/sweep-stale-sandboxes.py)
  lists workspaces, checks the `last_applied_at` tag against now, slacks
  the owner if older than 7 days, and runs `terraform destroy` after 14.

Wire it as a scheduled GitHub Action or an EventBridge → Lambda. The
script is idempotent and dry-run by default.

## 4. AWS Budgets per owner

[`budget-per-owner.tf`](budget-per-owner.tf) creates one AWS Budget per
distinct `Owner` tag, with email + Slack alerts at 50% / 80% / 100% of the
threshold. The threshold defaults to $50/month — enough for a developer
running the reference pipeline daily.

```bash
cd labs/03-iac-sandbox-environments
terraform init
terraform apply -var owner=$USER -var monthly_limit=50
```

This creates the budget once per owner; subsequent applies are idempotent.

---

## What "done" looks like

- A second developer running `terraform apply` with their own
  `TF_WORKSPACE` doesn't conflict with yours.
- A PR closing triggers a workflow that destroys its sandbox in ~5
  minutes.
- A `terraform apply` without `TF_VAR_owner` set fails fast with a clear
  error.
- The AWS Budgets dashboard shows a per-owner line item for sandbox spend.
- Forgetting to clean up costs you a Slack DM after 7 days, not a
  surprise invoice.

## Common pitfalls

- **Bucket-name collisions.** S3 bucket names are global; two sandboxes
  with the same workspace name in different accounts will collide. The
  module suffixes the bucket name with `account_id` to keep this rare,
  but don't pick `default` as a workspace name.
- **State drift on destroy failure.** If `terraform destroy` half-succeeds
  (e.g., a non-empty bucket blocks the bucket delete), the workspace
  state is partial. The teardown workflow runs `aws s3 rm --recursive`
  before retrying — see [`scripts/empty-bucket.sh`](../../scripts/empty-bucket.sh).
- **Deeply nested data.** A `terraform destroy` on an Iceberg table that
  has been written to by a Glue job needs the curated bucket emptied first
  (Iceberg metadata files block the bucket delete). The teardown script
  knows about this.
- **"It's just a sandbox" thinking.** Sandboxes use real services with
  real IAM. A sandbox with a misconfigured public bucket is still a
  public bucket. Lab 4's policy gates apply to sandbox plans, not just
  prod.
