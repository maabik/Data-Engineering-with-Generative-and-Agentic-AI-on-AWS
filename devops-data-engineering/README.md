# DevOps for Data Engineering on AWS

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

An opinionated, hands-on companion chapter codebase for the rest of the book.
Where the other chapter codebases teach **what** to build (lakehouses, agents,
RAG pipelines, warehouses), this chapter codebase teaches **how to ship and
operate it safely** when the thing you're shipping is a data pipeline, an
orchestrator, or an agent — not a stateless web service.

The pattern most data teams default to — point-and-click in the AWS console,
Glue jobs edited in place, Airflow DAGs uploaded by hand, agents redeployed
from a laptop — does not survive contact with multiple environments, multiple
contributors, or auditors. This chapter replaces that pattern with one your
software-engineering peers would recognise:

```
git → pre-commit hooks → CI checks → ephemeral sandbox → policy gates → prod deploy → observability + rollback
```

…adapted for the realities of data work: stateful pipelines, slow integration
tests against real services, expensive sandboxes, and LLM/agent components
that are non-deterministic by design.

The chapter codebase is **AWS-first** and **Terraform-primary**. CDK appears
as a side-by-side comparison in [`reference-pipeline/cdk/`](reference-pipeline/cdk/)
so you can see the same pipeline expressed both ways. CloudFormation is
covered briefly in [`docs/iac-tool-tradeoffs.md`](docs/iac-tool-tradeoffs.md).

---

## Who this is for

- Data engineers asked to "make this production-grade" and unsure where to
  start.
- Platform / DevOps engineers onboarding a data team and wanting a concrete
  blueprint instead of generic CI/CD advice.
- Tech leads who already use Terraform/CDK for application code but haven't
  applied the same rigor to Glue jobs, Step Functions, MWAA DAGs, AgentCore
  runtimes, or MCP servers.
- Anyone who has felt the specific pain of "the DAG worked on my laptop, it
  doesn't work in MWAA, I have no idea why."

If you've worked through `01_intro/` and at least one of the
`05_bigdata/`, `06_orchestration/`, or `12_agentic/` chapter codebases, you
have the context this chapter codebase assumes.

---

## What you'll build

One reference pipeline (in [`reference-pipeline/`](reference-pipeline/)) and
nine labs that progressively wrap it in DevOps practice:

| # | Lab | What you wire up |
|---|-----|------------------|
| 1 | [Version-control foundations](labs/01-version-control-foundations/) | Trunk-based branching for data code, monorepo vs. polyrepo tradeoffs, CODEOWNERS for IaC + DAGs, conventional commits, semantic versioning of pipeline artifacts. |
| 2 | [Pre-commit security hooks](labs/02-pre-commit-security-hooks/) | `pre-commit` config with `gitleaks`, `detect-secrets`, `tflint`, `tfsec`, `checkov`, `bandit`, `ruff`, `sqlfluff`, plus a custom hook that blocks `.ipynb` outputs and AWS account IDs. |
| 3 | [IaC sandbox environments](labs/03-iac-sandbox-environments/) | Per-developer ephemeral AWS sandboxes with Terraform workspaces, `TF_VAR_owner` blast-radius tagging, automatic teardown on PR close, cost guardrails via AWS Budgets. |
| 4 | [Policy-as-code gates](labs/04-policy-as-code/) | OPA/Conftest + Checkov policies that block public S3 buckets, ungoverned IAM `*` actions, untagged Glue jobs, and Bedrock model invocations without guardrails. |
| 5 | [Pipeline testing strategy](labs/05-pipeline-testing-strategy/) | The four test layers a data pipeline actually needs: unit (PySpark, business logic), contract (schema, data contracts), integration (real S3/Glue against a sandbox), and post-deploy smoke. |
| 6 | [CI/CD with GitHub Actions](labs/06-ci-cd-github-actions/) | Reusable workflows for plan/apply, environment-scoped concurrency, manual approvals via `environments`, drift detection on a schedule, and PR-comment plan summaries. |
| 7 | [Secrets and OIDC](labs/07-secrets-and-oidc/) | Replace long-lived AWS keys in CI with GitHub OIDC → IAM role assumption, scoped per environment; secrets in AWS Secrets Manager with rotation, never in repo or in `tfvars`. |
| 8 | [Observability and rollback](labs/08-observability-and-rollback/) | CloudWatch + structured logging conventions for Glue / Step Functions / MWAA / AgentCore, deploy-tied SLOs, automated rollback on data-quality alarm breach, and incident-replay via Iceberg time travel. |
| 9 | [Agentic and MCP DevOps](labs/09-agentic-and-mcp-devops/) | Versioning prompts as code, regression-testing agents in CI (eval harnesses, prompt-injection suite, deterministic fixtures), guardrail attachment as a deploy precondition, MCP server deploy patterns. |

Each lab has its own README that's self-contained — you can drop into lab 6
without doing 1–5, though they compose into a coherent pipeline if you do
them in order.

---

## The reference pipeline

[`reference-pipeline/`](reference-pipeline/) is one pipeline expressed in both
Terraform (primary) and CDK (comparison), small enough to run end-to-end in a
sandbox account but realistic enough to exercise every DevOps practice in the
labs:

```
S3 (raw)  →  Glue ingestion job  →  S3 (curated, Iceberg)  →  Step Functions
                                                              ↓
                                                          Glue DQ job (Deequ)
                                                              ↓
                                                          Athena workgroup
                                                              ↓
                                                  Strands agent on AgentCore
                                                  (answers questions over the
                                                   curated table)
```

It's deliberately a slice of the architecture from chapter codebases 5, 6,
and 12 so the DevOps lessons here transfer directly to those chapter
codebases' projects.

```bash
# Deploy the reference pipeline to a sandbox
cd reference-pipeline/terraform/envs/sandbox
terraform init
terraform plan -out plan.tfplan
terraform apply plan.tfplan

# Tear it down when you're done
terraform destroy
```

See [`reference-pipeline/README.md`](reference-pipeline/README.md) for the
full deploy/verify/teardown cycle and the CDK equivalent.

---

## How the labs are structured

Each lab directory contains:

- `README.md` — narrative: the problem this lab solves, why it matters in a
  data-engineering context (not a generic web-app context), what to do, and
  what "done" looks like.
- Working configuration / scripts — `pre-commit-config.yaml`, GitHub Actions
  workflow templates, OPA policies, etc. Drop them into your own repo and
  they should work.
- A "common pitfalls" section at the bottom — things that bit real data
  teams, not theoretical risks.

The labs are designed to be **adopted incrementally** in your own repos.
You don't need to do all nine to get value; pick the ones whose pain points
match yours.

---

## Repo layout

```
devops-data-engineering/
├── README.md                          # this file
├── docs/                              # cross-cutting reference material
│   ├── iac-tool-tradeoffs.md          # Terraform vs CDK vs CloudFormation for data eng
│   ├── data-pipeline-environments.md  # dev / sandbox / staging / prod for stateful pipelines
│   ├── testing-data-pipelines.md      # unit/contract/integration/smoke for data work
│   └── agentic-devops-patterns.md     # what's different about shipping agents
├── reference-pipeline/
│   ├── README.md
│   ├── terraform/                     # primary: HCL + workspaces
│   │   ├── envs/{sandbox,prod}/
│   │   └── modules/{networking,glue-pipeline,orchestration,observability}/
│   ├── cdk/                           # comparison: TypeScript CDK of the same pipeline
│   ├── glue_jobs/                     # PySpark source + unit tests
│   └── tests/{unit,integration}/
├── labs/
│   ├── 01-version-control-foundations/
│   ├── 02-pre-commit-security-hooks/
│   ├── 03-iac-sandbox-environments/
│   ├── 04-policy-as-code/
│   ├── 05-pipeline-testing-strategy/
│   ├── 06-ci-cd-github-actions/
│   ├── 07-secrets-and-oidc/
│   ├── 08-observability-and-rollback/
│   └── 09-agentic-and-mcp-devops/
├── .github/workflows/                 # the CI workflows the labs reference
└── scripts/                           # helper scripts (sandbox bootstrap, OIDC role, drift check)
```

---

## Prerequisites

- The base AWS / CLI / AI-assistant setup from [`../README.md`](../README.md)
  and [`../01_intro/aidlc-setup/`](../01_intro/aidlc-setup/).
- A **sandbox AWS account** — the labs intentionally exercise IAM,
  Lake Formation, and KMS in ways that are hard to clean up against a shared
  account.
- Local tools (the labs install the rest as needed):
  ```bash
  brew install terraform tflint tfsec checkov pre-commit gitleaks awscli
  pipx install detect-secrets sqlfluff ruff
  npm i -g aws-cdk            # optional, only for the CDK comparison
  ```
- A **GitHub repo** you can push to. The CI labs assume GitHub Actions; the
  patterns translate to GitLab CI or CodePipeline with mechanical changes,
  noted where relevant.

---

## What this chapter codebase is *not*

- **Not a Terraform / CDK tutorial.** It assumes you can read HCL and
  TypeScript and reach for the docs when a resource type is new. If you've
  never written `aws_s3_bucket`, do a Terraform fundamentals tutorial first.
- **Not a general DevOps survey.** It only covers what's specific to data
  pipelines, orchestrators, and agents. Topics like blue/green deploys for
  stateless web services are out of scope; topics like "how do I roll back a
  schema change to an Iceberg table without losing time-travel history" are
  in scope.
- **Not multi-cloud.** AWS-only. The reference pipeline uses S3, Glue, Step
  Functions, Athena, AgentCore. Most patterns generalise; the YAML doesn't.

---

## Cost notes

The reference pipeline running for an hour in a sandbox is on the order of a
few cents (S3 storage, Glue DPU-hours, AgentCore runtime). The labs that
exercise CI add zero AWS cost — GitHub Actions free-tier minutes are plenty.
The exception is lab 3 (sandbox environments) if you forget to teardown:
that's why lab 3 ships with auto-teardown via PR-close events and an AWS
Budgets alarm wired into the sample.

Run `terraform destroy` and the lab-specific teardown scripts when you're
done. Each lab's README ends with the exact teardown command.

---

## Where to start

- New to DevOps for data work? → start at lab 1, work forward.
- Already have CI/CD but no policy gates? → jump to lab 4, then 6.
- Shipping agents and want to lock down what an LLM can deploy? → lab 9,
  then back-fill 4 and 7.
- Inheriting a data platform with no IaC at all? → read
  [`docs/data-pipeline-environments.md`](docs/data-pipeline-environments.md)
  first, then lab 3, then lab 1.

## Default region

The reference pipeline defaults to **us-east-1** in
`reference-pipeline/envs/sandbox/main.tfvars`. Override with the
Terraform `-var aws_region=…` flag or by editing the tfvars before
`terraform apply`. The CDK side-by-side reads the region from the
active AWS profile.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| reference-pipeline | S3 buckets × 3 (raw, curated, scripts) | $0.0000 | $0.0001 | Pay-per-GB. |
| reference-pipeline | Glue Data Catalog (2 DBs) | $0.0000 | $0.0000 | First 1M objects free. |
| reference-pipeline | Glue jobs × 3 (G.1X, definitions only) | $0.0000 | $0.0050 | $0.44/DPU-hr only while running. |
| reference-pipeline | Step Functions state machine | $0.0000 | $0.0001 | Standard: $0.025/1k state transitions. |
| reference-pipeline | CloudWatch log group + dashboard + alarm | $0.0000 | $0.0010 | $0.10/alarm/month + log retention. |
| labs/01-version-control-foundations | (none) | $0.0000 | $0.0000 | Local config only. |
| labs/02-pre-commit-security-hooks | (none) | $0.0000 | $0.0000 | Local hooks only. |
| labs/03-iac-sandbox-environments | AWS Budgets per owner | $0.0000 | $0.0003 | First 2 budgets free, then $0.02/budget/day ≈ $0.0008/hr each. |
| labs/04-policy-as-code | (none) | $0.0000 | $0.0000 | OPA / Conftest / Checkov run locally. |
| labs/05-pipeline-testing-strategy | (none) | $0.0000 | $0.0000 | Tests run against an already-deployed sandbox. |
| labs/06-ci-cd-github-actions | (none) | $0.0000 | $0.0000 | GitHub Actions YAML — no AWS resources. |
| labs/07-secrets-and-oidc | IAM OIDC provider + 3 roles | $0.0000 | $0.0000 | IAM is free. |
| labs/08-observability-and-rollback | CloudWatch alarms (count varies) | $0.0000 | $0.0010 | $0.10/alarm/month per alarm; folded into reference-pipeline when wired. |
| labs/09-agentic-and-mcp-devops | (none) | $0.0000 | $0.0000 | Prompt files + eval datasets + Terraform snippets. |
| **Chapter total** | | **$0.0000** | **~$0.0085** | Reference pipeline pay-per-use; labs are mostly local tooling. |

This chapter has **zero standing hourly cost** — the reference
pipeline's Glue jobs and Step Functions executions are pay-per-use,
and most labs ship local tooling, OPA policies, or GitHub Actions YAML
templates rather than deployed AWS resources.

