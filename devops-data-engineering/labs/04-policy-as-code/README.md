# Lab 4 — Policy-as-code gates

The pre-commit suite in lab 2 catches lint-grade IaC mistakes (deprecated
arguments, missing tags). This lab catches **organisational policy
violations**: things that are syntactically valid IaC but that your team has
agreed should never ship to AWS.

Examples relevant to a data platform:

- "S3 buckets are never public, including sandboxes."
- "IAM policies never use `Action: '*'` outside of explicitly approved
  break-glass roles."
- "Glue jobs without an `Owner` tag are blocked."
- "Bedrock model invocations without an attached guardrail are blocked."
- "Iceberg tables in the curated zone always have KMS encryption enabled."

These are **gates**, not lints. They block `terraform apply`. The point
is to encode "we agreed X should never happen" once, in code, instead of
catching it during PR review (or, more often, in production).

## What you'll wire up

- **Conftest** + **OPA Rego policies** evaluated against `terraform show
  -json plan.tfplan`. Runs in CI; can run locally too.
- **Checkov custom policies** for the things Checkov already understands
  about AWS resource shapes (encryption, public access).
- A **CI gate** that fails the build on any high-severity violation.

The two tools overlap in scope on purpose. Checkov is faster to write
policies for (it knows the AWS resource shapes); Conftest is more flexible
for cross-resource rules ("any Bedrock model invocation must be paired
with a guardrail attachment in the same plan").

---

## 1. Conftest / OPA

[`policies/`](policies/) contains the Rego rules:

- [`policies/no_public_buckets.rego`](policies/no_public_buckets.rego)
- [`policies/no_iam_wildcard.rego`](policies/no_iam_wildcard.rego)
- [`policies/required_tags.rego`](policies/required_tags.rego)
- [`policies/bedrock_requires_guardrail.rego`](policies/bedrock_requires_guardrail.rego)
- [`policies/curated_kms_encryption.rego`](policies/curated_kms_encryption.rego)

Local run:

```bash
cd reference-pipeline/terraform/envs/sandbox
terraform plan -out plan.tfplan
terraform show -json plan.tfplan > plan.json

cd -
conftest test --policy labs/04-policy-as-code/policies plan.json
```

CI runs the same two-step pattern — see
[`../06-ci-cd-github-actions/workflow-templates/plan-and-policy.yml`](../06-ci-cd-github-actions/workflow-templates/plan-and-policy.yml).

## 2. Checkov custom policies

For the rules Checkov already covers (`CKV_AWS_*` IDs), enable them in
[`.checkov.yaml`](.checkov.yaml). For team-specific rules, write custom
checks in [`checkov-custom/`](checkov-custom/).

## 3. The gate

Add this to the PR workflow (already in lab 6's templates):

```yaml
- name: Conftest policy gate
  run: |
    terraform -chdir=reference-pipeline/terraform/envs/sandbox plan -out plan.tfplan
    terraform -chdir=reference-pipeline/terraform/envs/sandbox show -json plan.tfplan > plan.json
    conftest test --policy labs/04-policy-as-code/policies plan.json
- name: Checkov policy gate
  run: |
    checkov -d reference-pipeline/terraform/ \
      --config-file labs/04-policy-as-code/.checkov.yaml \
      --quiet --compact
```

A failure fails the PR check. Reviewers see the policy violation, not a
generic "checkov failed."

---

## Why this layer matters specifically for data work

- **Lake Formation grants are easy to get wrong.** A grant is one HCL
  block; a typo changes the principal from one analyst to ALL analysts.
  Policy-as-code can require that any `aws_lakeformation_permissions`
  resource references a principal in an approved list.
- **Glue table creation is easy to do without encryption.** Default
  encryption settings on a Glue catalog database don't apply
  automatically; a job creating a table without specifying encryption
  produces an unencrypted table. A policy fails the plan if the job
  doesn't pass `encryption_configuration`.
- **Bedrock invocations bypass guardrails by default.** The model is
  callable directly. The `02_security/guardrails-from-policy/` chapter
  ships a guardrail; this lab's `bedrock_requires_guardrail.rego`
  enforces that any IAM role that grants `bedrock:InvokeModel` also has a
  guardrail-attachment statement in the same plan.
- **Sandboxes deploy real resources.** Don't skip the policy gate for
  sandbox plans. The point of sandboxes is to catch problems with
  production-shaped configs early.

---

## What "done" looks like

- A PR that adds an `aws_s3_bucket` with `acl = "public-read"` fails the
  Conftest gate with a clear message naming the resource and the policy.
- A PR that adds an IAM policy with `Action: "*"` fails with the same
  shape of message.
- A PR adding a Glue job without an `Owner` tag fails with
  `required_tags`.
- A PR adding a Bedrock IAM role without a guardrail attachment fails
  with `bedrock_requires_guardrail`.

## Common pitfalls

- **Writing policies against HCL source instead of plan JSON.** Always
  evaluate against the plan output. Plans expand `default_tags` and
  resolve module inputs; HCL doesn't.
- **Failing closed too aggressively.** A new policy that retroactively
  flags 30 existing resources will be disabled within a week. Roll out
  with `--severity high` first; tighten progressively.
- **Treating warning as a failure mode.** Make every policy either
  "pass" or "deny". Warning levels are noise — reviewers stop reading
  them. If it shouldn't ship, deny it; otherwise, drop the rule.
- **Forgetting the unit test.** Each Rego policy should ship with a
  `*_test.rego` that asserts on a fixture plan input. Without tests, the
  policies bit-rot.
