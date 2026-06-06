# Lab 7 — Secrets and OIDC

The default way most teams give CI access to AWS — long-lived `AKIA...`
keys stored as repo secrets — is the wrong default. This lab replaces it
with **GitHub OIDC → IAM role assumption**, scoped per environment, no
long-lived credentials anywhere.

It also covers the data-engineering-specific secrets (database
credentials, third-party API keys, model provider keys) and where they
should live: AWS Secrets Manager with rotation, never `tfvars` and never
environment-scoped GitHub secrets.

## Why OIDC

- **No long-lived keys to leak.** A leaked OIDC token is valid for
  minutes; a leaked access key is valid until rotated.
- **Granular trust.** The IAM role's trust policy can require a specific
  GitHub repo, branch, environment, and workflow file. A staging deploy
  workflow on `main` can assume the staging role; a fork-PR workflow
  cannot.
- **Auditable in CloudTrail.** Each assumed-role session is tagged with
  the GitHub run ID, so you can correlate API calls back to a specific
  workflow execution.

## Architecture

```
GitHub Actions runner
    │  presents OIDC JWT
    ▼
AWS IAM OIDC provider (one per AWS account)
    │  validates issuer = github
    ▼
sandbox-deploy-role / staging-deploy-role / prod-deploy-role
    │  trust policy: only specific repo + ref + environment
    ▼
short-lived credentials (1 hour) — used by terraform
```

## What's in this lab

- [`oidc-provider.tf`](oidc-provider.tf) — the AWS-side OIDC provider
  resource, deploy this **once per AWS account**.
- [`deploy-role.tf`](deploy-role.tf) — the IAM role + trust policy
  template. Deploy three copies, one per environment, each with a
  different `environment_name` and `branch_pattern`.
- [`scripts/bootstrap-oidc.sh`](scripts/bootstrap-oidc.sh) — convenience
  script that runs the apply for both files in the right order.

## How to deploy

For each AWS account (sandbox, staging, prod), run:

```bash
cd labs/07-secrets-and-oidc
terraform init

# 1. The OIDC provider — only one per account, idempotent
terraform apply -target=aws_iam_openid_connect_provider.github

# 2. The role for this environment
terraform apply \
  -var environment_name=sandbox \
  -var github_repo=your-org/your-repo \
  -var branch_pattern="*"

# Output: the role ARN — paste into GitHub repo settings, environment
# secrets, as SANDBOX_DEPLOY_ROLE.
```

For prod, the trust policy is tighter — only release tags can assume it:

```bash
terraform apply \
  -var environment_name=prod \
  -var github_repo=your-org/your-repo \
  -var ref_pattern="refs/tags/v*"
```

## Where each kind of secret should live

| Secret type | Where it lives | Rotation |
|---|---|---|
| AWS deploy credentials (CI) | OIDC; no stored secret | n/a |
| Database password | Secrets Manager, attached to the consuming role | Lambda rotator on schedule |
| Third-party API keys (Snowflake, Datadog) | Secrets Manager | Manual on key rotation |
| LLM provider keys (Anthropic, OpenAI) | Secrets Manager; agent role grants `secretsmanager:GetSecretValue` | Manual; key rotation should also rotate the eval suite's recorded baselines |
| Webhook URLs / Slack tokens | GitHub Actions secrets (used only in CI) | Manual |

## What does NOT go in `tfvars`

Even if `tfvars` files are gitignored:

- Database passwords — fetch via `aws_secretsmanager_secret_version`
  data source.
- API keys — same.
- Account IDs — use `data.aws_caller_identity.current` so the IaC works
  in any account.

The pre-commit `block-aws-account-ids` hook in lab 2 enforces the
account-ID rule at commit time.

## Common pitfalls

- **One global deploy role.** Convenient but defeats the point. Three
  roles, each scoped to its environment.
- **Trust policy with `repo:*`.** Overly broad. Specify
  `repo:your-org/your-repo:ref:refs/heads/main` (or the ref pattern you
  actually need).
- **`pass_role` in IAM not scoped.** A deploy role can `iam:PassRole`
  any role in the account by default — let it pass only the application
  roles it actually creates. The included `deploy-role.tf` does this.
- **Long-running build steps that exceed credential lifetime.** OIDC
  credentials default to 1 hour. A Glue job that takes 90 minutes will
  break credential refresh. Either bump session duration in the IAM
  role, or do the long-running work as a fire-and-forget API call (e.g.,
  `start_job_run` then poll).
- **Secrets Manager hidden costs.** Each secret is $0.40/month. Don't
  put per-PR sandbox secrets in Secrets Manager — generate ephemeral
  credentials in the deploy.
