# 05 — Big-data ETL and the agentic pipeline

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

The most ambitious chapter in the book. Starts with classical Glue ETL
(API ingestion → DataBrew profiling → Deequ data quality), then chains
five **Strands agents on Bedrock AgentCore Runtime** that profile,
contract, design, quality-check, and remediate datasets autonomously —
each agent producing JSON the next consumes. A Kiro spec-driven pipeline
appears alongside as an alternative authoring style.

## Default region

All projects default to **us-east-1**. Each `deploy.sh` and
`teardown.sh` honors `AWS_REGION=…` to override; the orchestrator in
chapter 12 will pick up specialists in whatever region you deployed them
to. Stay consistent within a single agent fleet.

```bash
# Deploy a specialist in us-east-1 (default)
cd 05_bigdata/data-profiling-agent && bash deploy.sh

# Or override
AWS_REGION=eu-west-1 bash deploy.sh
```

## Projects

| Project | What it deploys |
|---|---|
| [`lambda-api-ingestion/`](lambda-api-ingestion/) | Lambda that pulls JSON from a third-party API and writes it to the lake's landing zone. |
| [`glue-databrew-profiling/`](glue-databrew-profiling/) | Lambda that creates and runs a Glue DataBrew profiling job over a landed dataset. |
| [`glue-deequ-data-quality/`](glue-deequ-data-quality/) | Glue PySpark job that applies Amazon Deequ constraints to curated data. |
| [`api-integration-agent/`](api-integration-agent/) | Strands agent on AgentCore Runtime that takes a target dataset and an API spec, then writes, deploys, schedules, and live-tests an ingestion Lambda. |
| [`data-profiling-agent/`](data-profiling-agent/) | Triggers on `s3://.../landing/*`, runs DataBrew + an LLM analysis, emits an insights JSON. |
| [`data-contract-agent/`](data-contract-agent/) | Triggers on the same `insights/` prefix as the architect agent. Authors a dbt-flavoured YAML contract on first run; on subsequent runs diffs the contract against the live Glue table and classifies findings as breaking/warning/info. Compiles value-level clauses to Deequ checks the data-quality-deequ-agent picks up. |
| [`data-architect-agent/`](data-architect-agent/) | Consumes the profiling agent's insights, designs the schema, registers Glue tables, runs the first ETL. |
| [`data-quality-deequ-agent/`](data-quality-deequ-agent/) | Runs Deequ constraint suggestions + verification, registers the report, emits quality insights JSON. |
| [`data-quality-agent/`](data-quality-agent/) | Investigates failed constraints and writes a remediation plan an engineer can pick up. |
| [`kiro-data-pipeline/`](kiro-data-pipeline/) | Same problem set, IDE-spec authoring style, useful as a comparison. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| lambda-api-ingestion | Lambda + SQS + Secrets Manager | $0.0006 | $0.0010 | $0.40/secret/month ≈ $0.0006/hr; everything else pay-per-invoke. |
| glue-databrew-profiling | Lambda + DataBrew job | $0.0000 | $0.0050 | DataBrew billed at $1.00/profile-run when triggered. |
| glue-deequ-data-quality | Glue 2-DPU job (definition only) | $0.0000 | $0.0050 | $0.44/DPU-hr while running; ~$0/hr at rest. |
| kiro-data-pipeline | Glue Iceberg job (definition only) | $0.0000 | $0.0050 | Same Glue pricing; idle definitions free. |
| api-integration-agent | AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving traffic. |
| api-integration-agent | AgentCore Gateway + Cognito + Lambda + Secrets Manager | $0.0006 | $0.0050 | Gateway pay-per-call; Cognito free at demo MAU. |
| data-profiling-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| data-profiling-agent | DataBrew + S3 + Lambda + Cognito + Gateway | $0.0006 | $0.0100 | DataBrew profile per invocation. |
| data-contract-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| data-contract-agent | Bedrock + Cognito + Gateway + S3 (read-only against Glue) | $0.0006 | $0.0030 | Bedrock per LLM call; no Glue/Athena writes. |
| data-architect-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| data-architect-agent | Glue catalog + Bedrock + Cognito + Gateway | $0.0006 | $0.0050 | Bedrock per LLM call. |
| data-quality-deequ-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| data-quality-deequ-agent | Glue Deequ job + Athena + Cognito + Gateway | $0.0006 | $0.0100 | Glue + Athena pay-per-run. |
| data-quality-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| data-quality-agent | Athena lookups + Cognito + Gateway | $0.0006 | $0.0050 | Athena $5/TB scanned. |
| **Chapter total** | | **~$0.6522** | **~$0.7120** | Six AgentCore Runtimes are the dominant line. |

The chapter's **standing cost is dominated by the six AgentCore Runtimes**
($0.108/hr each while actively serving). Tear down any specialist you
aren't using:

```bash
cd 05_bigdata/<agent-folder> && bash teardown.sh
```

The four classical-ETL projects (Lambda, DataBrew, Glue, Kiro) bill only
when invoked — leaving them deployed is essentially free.
