# Reference pipeline

One pipeline, deployed two ways (Terraform primary, CDK side-by-side), used
as the test subject for every lab in this chapter codebase.

## Architecture

```
S3 (raw)  ─►  Glue ingestion job  ─►  S3 (curated, Iceberg)  ─►  Step Functions
                                                                     │
                                                                     ├─► Glue DQ job (Deequ)
                                                                     │
                                                                     └─► Athena workgroup
                                                                              │
                                                                              ▼
                                                              Strands agent on AgentCore
                                                              (sales-reporter; calls MCP
                                                               server backed by Athena)
```

It's a deliberate slice of `05_bigdata/` (Glue jobs, DQ),
`06_orchestration/` (Step Functions), and `12_agentic/` (AgentCore + MCP).
The labs reference its files directly.

## Terraform layout

```
terraform/
├── envs/
│   ├── sandbox/      # the env you'll use 95% of the time
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── backend.tf
│   │   └── terraform.tfvars
│   └── prod/
│       ├── main.tf
│       ├── variables.tf
│       ├── backend.tf
│       └── terraform.tfvars
└── modules/
    ├── networking/        # VPC, endpoints, security groups
    ├── glue-pipeline/     # raw + curated buckets, Glue jobs, catalog DBs
    ├── orchestration/     # Step Functions state machine
    └── observability/     # CloudWatch dashboards, alarms, log groups
```

Each `envs/<env>/` is a thin composition of the modules with environment-
specific variables. **Modules are identical across environments** — that's
the rule in [`../docs/data-pipeline-environments.md`](../docs/data-pipeline-environments.md).

## Deploy to sandbox

```bash
# One-time: pick a workspace name (or use TF_WORKSPACE=$USER-default)
export TF_WORKSPACE=$USER-ref
export TF_VAR_owner=$USER

cd reference-pipeline/terraform/envs/sandbox
terraform init
terraform workspace new $TF_WORKSPACE 2>/dev/null || terraform workspace select $TF_WORKSPACE
terraform plan -out plan.tfplan
terraform apply plan.tfplan

# Run the pipeline once to verify
terraform output -raw step_functions_arn | xargs -I{} aws stepfunctions start-execution --state-machine-arn {}
```

Expected output: a curated Iceberg table appears in the Glue catalog under
the database `${TF_WORKSPACE}_curated`, queryable via Athena, with a
non-zero row count.

## Tear down

```bash
terraform destroy
terraform workspace select default
terraform workspace delete $TF_WORKSPACE
```

If the destroy stalls on a non-empty S3 bucket, the labs include a
`scripts/empty-bucket.sh` helper. Don't manually delete from the console —
that leaves Terraform state out of sync.

## CDK comparison

[`cdk/`](cdk/) deploys the same pipeline using the AWS CDK in TypeScript.
It's there for shape comparison, not as a recommended path. See
[`../docs/iac-tool-tradeoffs.md`](../docs/iac-tool-tradeoffs.md) for the
"when would I pick this instead" discussion.

```bash
cd reference-pipeline/cdk
npm install
npx cdk diff --context env=sandbox --context owner=$USER
npx cdk deploy --context env=sandbox --context owner=$USER
```

## What lives where

- `glue_jobs/` — PySpark source for the ingestion + curation jobs. Jobs are
  written so the transformation is a pure function with a thin Glue wrapper
  around it (see [`../docs/testing-data-pipelines.md`](../docs/testing-data-pipelines.md)).
- `tests/unit/` — pytest unit tests against a local SparkSession.
- `tests/integration/` — pytest integration tests against the deployed
  sandbox; uses `terraform output -json` as fixtures.
- `agent/` (added by lab 9) — the Strands agent, prompts, MCP tool
  definitions, and eval datasets.
