# Lab 6 — CI/CD with GitHub Actions

This lab assembles the workflows that run everything from the prior labs:
the pre-commit suite, the policy gates, the unit/contract/integration
tests, the per-PR sandbox deploy, the merge-to-main staging deploy, and
the tag-to-prod deploy.

The reusable workflows live in [`workflow-templates/`](workflow-templates/).
Drop them into `.github/workflows/` in your own repo. They reference
secrets and AWS roles set up in lab 7 (OIDC).

## The shape of the pipeline

```
PR open / push           merge to main             tag v*
   │                           │                      │
   ▼                           ▼                      ▼
pre-commit ──► tests ──► policy gates ──► sandbox deploy ──► integration ──► smoke
                                              │
                                              ▼ (PR close)
                                          sandbox teardown
                                              │
                                              ▼ (on merge to main)
                                        staging deploy ──► smoke + DQ
                                              │
                                              ▼ (manual approval, on tag)
                                          prod deploy ──► smoke + DQ
                                              │
                                              ▼ (on DQ failure)
                                          automatic rollback
```

## Workflows in this lab

| File | Trigger | Purpose |
|---|---|---|
| [`pr.yml`](workflow-templates/pr.yml) | `pull_request` | pre-commit, tests, policy gate, plan |
| [`pr-deploy-sandbox.yml`](workflow-templates/pr-deploy-sandbox.yml) | `pull_request` | apply the PR's sandbox; run integration |
| [`merge-to-main.yml`](workflow-templates/merge-to-main.yml) | `push: main` | apply to staging, run smoke + DQ |
| [`release.yml`](workflow-templates/release.yml) | `release: published` | apply to prod (with approval), run smoke + DQ |
| [`drift-detection.yml`](workflow-templates/drift-detection.yml) | `schedule: daily` | `terraform plan` and Slack the diff |
| [`plan-and-policy.yml`](workflow-templates/plan-and-policy.yml) | reusable | the plan + policy gate, called by the others |

## Key patterns

### 1. Reusable workflows, not copy-paste

`plan-and-policy.yml` is a reusable workflow (`workflow_call`). The PR,
staging, and prod workflows all call it with different `environment` and
`aws-role` inputs. Don't fork the YAML per environment.

### 2. Environment-scoped concurrency

```yaml
concurrency:
  group: deploy-${{ inputs.environment }}
  cancel-in-progress: false
```

Two prod deploys can't race. Two staging deploys can't race. PR sandbox
deploys (one workspace each) don't conflict so concurrency isn't needed.

### 3. Manual approval on prod

GitHub `environments` (Settings → Environments → `prod` → Required
reviewers) gates the prod apply step. The reviewer sees the plan in the
job log before approving. The OIDC role `prod-deploy-role` is **only
referenced from the prod environment's secrets**, so the workflow can't
accidentally apply to prod from a non-prod job.

### 4. Plan output as a PR comment

`plan-and-policy.yml` writes the plan summary as a PR comment. Reviewers
review the diff alongside the code. Long plans get truncated to 60KB
(GitHub's comment limit); the full plan is uploaded as an artifact.

### 5. Drift detection

`drift-detection.yml` runs `terraform plan` on a daily schedule against
prod with `-detailed-exitcode` and Slacks the diff if exit code is 2
(changes detected without a `terraform apply` having run). Drift in prod
is almost always a sign of:

- A console click someone made and forgot to back-port to IaC.
- A resource auto-modified by AWS (Glue catalog updates from a running
  job — these are expected, exclude them).
- Provider version changes that re-render the same resource differently.

The workflow includes an exclude list for known-safe drift paths.

### 6. Cache the slow steps

```yaml
- uses: hashicorp/setup-terraform@v3
  with: { terraform_version: 1.8.5 }
- uses: actions/cache@v4
  with:
    path: ~/.terraform.d/plugin-cache
    key: tf-${{ hashFiles('**/.terraform.lock.hcl') }}
```

The provider download takes ~30 seconds. Caching shaves that off every
job. Same for the pre-commit hook environment.

## Secrets / OIDC

This lab uses three role ARNs, set up in lab 7:

- `SANDBOX_DEPLOY_ROLE` — scoped to the sandbox account.
- `STAGING_DEPLOY_ROLE` — scoped to staging.
- `PROD_DEPLOY_ROLE` — scoped to prod.

Each is an IAM role with a trust policy that only accepts an OIDC token
from your GitHub repo's `main` branch (or a tag, for prod). No long-lived
access keys.

Don't store these as repo-level secrets — they're environment-scoped
secrets so non-prod jobs can't read the prod role ARN.

## What "done" looks like

- A PR open spins up a sandbox in ~10 minutes and posts the plan as a
  comment.
- A PR close destroys the sandbox in ~5 minutes.
- A merge to main deploys to staging within 15 minutes.
- A tag deploys to prod after a human approval, with a rollback path
  ready (lab 8).
- A scheduled drift check posts a Slack message if anyone clicked
  something in the AWS console.

## Common pitfalls

- **One workflow that does everything.** Hard to debug, slow, kills CI
  budget. Break by trigger, share the work with `workflow_call`.
- **Long-lived AWS keys in `secrets`.** Lab 7 covers OIDC; do that first.
- **Approving the same PR twice.** GitHub environments' "Required
  reviewers" applies per-deploy, not per-PR. Configure it.
- **No timeout on integration jobs.** A hung Glue job can sit until the
  GitHub Actions 6-hour limit. Always set `timeout-minutes`.
- **Drift detection blocked on flaky resources.** Glue catalog
  metadata routinely shows up as drift. Maintain an exclude list rather
  than disabling drift detection.
