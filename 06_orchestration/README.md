# 06 — Orchestration

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Generative-AI-assisted authoring of AWS Step Functions pipelines.

## Projects

- [`step-function-generator/`](step-function-generator/) — S3-triggered Lambda
  that reads a natural-language prompt, calls Amazon Bedrock (Claude Messages
  API), validates the returned Amazon States Language JSON against an
  allowlist, and creates or updates a Step Functions state machine.
- [`pipeline-cost-attribution/`](pipeline-cost-attribution/) — Chargeback for
  shared-services data platforms: EventBridge-driven tag enforcer plus a
  Cost Explorer aggregator that lands per-team / per-BU / per-pipeline spend
  in Athena.
- [`text-to-dag-agent/`](text-to-dag-agent/) — Strands agent on AgentCore Runtime
  that turns natural-language pipeline descriptions into validated Step Functions
  ASL or Airflow DAGs, with Lambda tools exposed via AgentCore Gateway.
- [`log-analysis-agent/`](log-analysis-agent/) — EventBridge-triggered
  Strands agent on AgentCore Runtime that consumes a pipeline-run
  descriptor, tails the Airflow + Spark logs (S3 or CloudWatch), and
  emits a structured insights JSON to S3 plus a one-line headline to
  SNS for on-call.

## Default region

All projects default to **us-east-1**. Each `deploy.sh` and
`teardown.sh` honors `AWS_REGION=…` to override.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| step-function-generator | Lambda + S3 + IAM | $0.0000 | $0.0010 | S3-triggered Lambda; pay-per-invoke. |
| step-function-generator | Bedrock per generation | $0.0000 | $0.0020 | Claude generates ASL on demand. |
| text-to-dag-agent | AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving. |
| text-to-dag-agent | AgentCore Gateway + Cognito + Lambda | $0.0006 | $0.0050 | Gateway pay-per-call. |
| pipeline-cost-attribution | Lambda × 2 + S3 + Glue + Athena workgroup | $0.0000 | $0.0020 | All pay-per-use. |
| log-analysis-agent | AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving. |
| log-analysis-agent | Lambda × 2 + SNS + Cognito + Gateway | $0.0006 | $0.0100 | CloudWatch Logs scan + SNS pub. |
| **Chapter total** | | **~$0.2172** | **~$0.2360** | Two AgentCore Runtimes are the chapter's dominant cost. |

The two **AgentCore Runtimes** (`text-to-dag-agent`, `log-analysis-agent`) at
$0.108/hr each are the only meaningful idle costs in this chapter. Tear
either one down with `bash teardown.sh` to drop the chapter to
~$0.001/hr.

