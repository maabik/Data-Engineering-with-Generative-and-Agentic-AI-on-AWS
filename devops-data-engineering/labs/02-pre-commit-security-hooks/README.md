# Lab 2 — Pre-commit security hooks

The most cost-effective DevOps tool is one that prevents broken commits
from leaving a developer's laptop. Pre-commit hooks catch in 5 seconds what
CI catches in 5 minutes — and what reviewers catch in 5 hours.

This lab installs a pre-commit suite tuned for an AWS data pipeline
codebase: secrets scanning, IaC linting and security, Python style and
security, SQL style, and a couple of custom hooks for the data-specific
foot-guns (committed notebook outputs, hardcoded AWS account IDs).

## What you'll wire up

| Tool | What it catches |
|---|---|
| `gitleaks` | Secrets in code, history, and PR diffs |
| `detect-secrets` | Secrets in **new** files only (faster than a full history scan) |
| `tflint` | Terraform style, deprecated arguments, provider issues |
| `tfsec` | Known-insecure Terraform patterns (public buckets, wide-open SGs) |
| `checkov` | IaC + container security; richer rule set than tfsec |
| `bandit` | Python security smells (eval, shell=True, weak crypto) |
| `ruff` | Python linting + formatting in one tool |
| `sqlfluff` | SQL style and dialect-correct linting |
| `nbstripout` | Strips outputs from notebooks before they're committed |
| Custom: `block-aws-account-ids` | Blocks 12-digit numeric strings near `arn:aws:` or `aws_account_id` |

## Install

```bash
brew install pre-commit gitleaks tflint tfsec checkov
pipx install detect-secrets sqlfluff ruff bandit nbstripout

cp labs/02-pre-commit-security-hooks/.pre-commit-config.yaml ./
cp labs/02-pre-commit-security-hooks/.gitleaks.toml          ./
cp labs/02-pre-commit-security-hooks/.tflint.hcl             ./
cp labs/02-pre-commit-security-hooks/.sqlfluff               ./
cp labs/02-pre-commit-security-hooks/scripts/block-aws-account-ids.sh ./scripts/

pre-commit install                # installs the hook into .git/hooks
pre-commit install --hook-type commit-msg   # for commitlint from lab 1
```

Run once over the whole repo to baseline:

```bash
pre-commit run --all-files
```

The first run will surface findings already in the repo. Triage them — fix
the real ones; for unavoidable false positives, add an inline disable
comment with a justification rather than blanket-disabling the rule.

## Why these specific hooks for data work

- **`gitleaks` AND `detect-secrets`.** They overlap on purpose. Gitleaks
  scans the diff against a regex set tuned for cloud secrets (AWS,
  Snowflake, Databricks tokens). detect-secrets uses a baseline file so
  you can audit "this is intentional" once and not have it fire forever.
  Data repos accumulate database connection strings in fixtures; you want
  both.
- **`tfsec` AND `checkov`.** Checkov has a broader rule set including data
  services (Glue, Athena, Lake Formation) that tfsec doesn't always cover.
  tfsec is faster on small diffs.
- **`sqlfluff`.** The most-edited language in many data repos isn't Python
  or HCL — it's SQL. Style consistency matters in queries that run
  millions of times.
- **`nbstripout`.** Notebooks with outputs are the worst commits in
  data-eng repos. They balloon diffs, leak data accidentally, and make
  reviews unreadable. Stripping outputs at commit time is non-negotiable.
- **Custom `block-aws-account-ids`.** No tool catches "I hardcoded
  123456789012 into a Terraform resource" reliably. A 5-line shell hook
  does.

## Files in this lab

- [`.pre-commit-config.yaml`](.pre-commit-config.yaml) — the pre-commit
  configuration. Drop at repo root.
- [`.gitleaks.toml`](.gitleaks.toml) — gitleaks ruleset; allowlists for
  test fixtures; tuned for AWS / Snowflake / Bedrock keys.
- [`.tflint.hcl`](.tflint.hcl) — tflint config with the AWS plugin.
- [`.sqlfluff`](.sqlfluff) — sqlfluff config; default to Athena/Trino
  dialect.
- [`scripts/block-aws-account-ids.sh`](scripts/block-aws-account-ids.sh) —
  custom hook script.

## What "done" looks like

- `git commit` on a file with `AKIA...` in it fails before the commit
  lands.
- `git commit` on a `.tf` file with a public-acl bucket fails with a
  tfsec error.
- A notebook commits with an empty `cell_outputs` (nbstripout ran).
- A 12-digit AWS account ID anywhere in a `.tf` or `.tfvars` file fails
  with a clear message.
- Running `pre-commit run --all-files` passes on the chapter codebase as
  shipped (it does, by design — the included config is what was used to
  build it).

## Common pitfalls

- **Slow hooks ignored locally.** If the hook suite takes >10 seconds,
  developers will start `--no-verify`. Keep the most expensive hooks
  (checkov full scan) for CI; the pre-commit set should be <5s on a small
  diff.
- **Auto-fix without diff review.** `ruff format` and `sqlfluff fix` will
  rewrite files. Configure them as `--check` in pre-commit and have the
  developer run the fix manually, not as a side effect of `git commit`.
- **Allowlists that grow forever.** Every gitleaks / detect-secrets
  allowlist entry should have a comment with the reason. Periodic audit;
  prune dead entries.
- **Skipping pre-commit in CI.** CI should re-run the hooks (`pre-commit
  run --all-files`) so a `--no-verify` push is still caught at PR time.
  The workflow in [`../06-ci-cd-github-actions/workflow-templates/`](../06-ci-cd-github-actions/workflow-templates/)
  includes this step.
