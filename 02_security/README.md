# 02 — Data Security and Governance

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Code samples for Chapter 2. Self-contained folders grouped by the chapter's
core data-protection topics: encrypting data in transit with TLS, encrypting
data at rest with customer-managed keys, enforcing fine-grained access to
governed data, and securing MCP servers with OAuth 2.1 or SigV4.

## Folders

### `tls-load-balancer/`

Covers the chapter's "encrypt data-in-transit" excerpt. A deployable
CloudFormation sample that wires Route 53 → ACM → Application Load Balancer
with an HTTPS listener and HTTP→HTTPS redirect. Contains `tls-alb.yaml` (the
template), `guide.md` (parameter-by-parameter walkthrough designed to be
driven by an AI assistant), and a README with deploy, verify, and teardown
commands. Use this folder to exercise whichever assistant you set up in
`01_intro/aidlc-setup/`.

### `encryption-kms/`

Covers the chapter's "Encryption for Data Protection" section. A Jupyter
notebook (`s3-kms-cmk.ipynb`) creates a customer-managed KMS key (CMK), then
creates an S3 bucket and applies default SSE-KMS encryption using that key.
Use this to practice the customer-managed key workflow the book contrasts with
AWS-owned keys.

### `fine-grained-access/`

Covers the chapter's "Fine-Grained Access Control for Data" section. Three
samples that go beyond IAM-level bucket access:

- `access_control_lake_formation.py` — grant and revoke column-scoped SELECT
  permissions on a Lake Formation table via `boto3`.
- `column_level_security_redshift.sql` — Redshift column-level GRANTs that
  expose non-sensitive columns to a service role while hiding credit card data.
- `row_level_security_redshift.sql` — Redshift RLS policy that filters rows by
  `customer_name` for a sales-rep role.

### `mcp-oauth-reference-server/`

Covers the chapter's "Model Context Protocol (MCP) Security" section. A
full OAuth 2.1 + PKCE reference implementation for MCP with Cognito as the
IdP: Dynamic Client Registration (RFC 7591) via a FastAPI proxy that fronts
Cognito, token introspection and refresh, a streamable-HTTP MCP resource
server with JWT validation and scope enforcement, and an end-to-end reference
client. Ships with a CloudFormation template for the Cognito side and is
reusable as a starter for any MCP server. The book describes the protocol;
this implements it cleanly.

### `guardrails-from-policy/`

Ingests a corporate data-classification or AUP policy document (PDF, DOCX, or
TXT) and generates a deployable Amazon Bedrock Guardrails configuration.
Claude reads the policy prose and extracts denied topics, blocked phrases, PII
entity types, content filter strengths, and contextual grounding thresholds,
then maps them deterministically to the `bedrock:CreateGuardrail` API shape.
Ships with a `policy2guardrail` CLI, a Python API, idempotent create-or-update
deployer, 57 unit tests (no AWS creds required), and a realistic sample ACME
AUP policy for demonstration. Demonstrates the meta-pattern of "policy as
code, via LLM."

### `prompt-injection-test-harness/`

A runnable test suite of 210 prompt-injection payloads across 10 attack
categories (direct override, role-play jailbreaks, system-prompt exfil,
indirect RAG injection, encoding obfuscation, tool abuse, social engineering,
context manipulation, and Bedrock-specific attacks) plus a benign baseline.
The harness targets any Bedrock model via the Converse API, any deployed
Bedrock Agent, or any custom callable (e.g. a Strands agent). Ships with a
CLI (`pi-harness run/list`), canary-based exfil detection, JSON and JUnit XML
output, 64 pytest unit tests (no AWS creds needed), and a GitHub Actions
workflow with a live-model gate behind `workflow_dispatch`. Designed to drop
into a CI pipeline.

### `sigv4-mcp-bridge/`

Covers the chapter's "IAM Authentication with SigV4 for MCP" section.
A drop-in Python middleware that lets any Strands agent authenticate to MCP
servers via SigV4 instead of OAuth — intended for purely internal AWS-to-AWS
agent traffic where the caller identity is already an IAM principal. Wraps
`aws/mcp-proxy-for-aws` in a Strands-specific factory, ships a Lambda-hosted
MCP target behind a Function URL (`AuthType: AWS_IAM`) via CloudFormation,
and includes both a full Bedrock-backed agent and a credential-only smoke
test.

### `bedrock-audit-trail/`

A deployable reference implementation that turns native Bedrock invocation logs
into a governance-grade audit trail. A CDK stack provisions KMS-encrypted S3
and CloudWatch log destinations, a Firehose delivery stream with a transform
Lambda that runs Comprehend PII detection and reversible tokenization, and an
Iceberg dataset on S3 partitioned by date/model/tenant. Includes a Glue Data
Catalog table, six Athena query templates (cost-per-tenant, top users, PII
rate, latency P50/P95/P99, guardrail trigger rates, jailbreak detections), a
four-sheet QuickSight dashboard template, an agent-trace correlation library
(`BedrockTracer`) that injects `user_id`/`tenant_id`/`trace_id` into every
Bedrock call, five CloudWatch alarms, and an optional cross-account S3
replication pattern. Ships with 56 unit tests (no AWS creds required). Ties to
Chapter 3 (Iceberg/data lake) and Chapter 11 (QuickSight).

---

## FinOps for Generative AI

Four projects that turn AI cost visibility into enforcement. See
[`finops/README.md`](finops/README.md) for the full overview.

### `finops/ai-finops-data-lake/`

Foundation layer: ingests Bedrock cost and usage signals into a queryable
Iceberg data lake. All other FinOps projects read from here.

### `finops/agent-run-cost-tracer/`

Closes the agentic AI visibility gap — attributes cost per agent run across
multi-step, multi-model executions, a dimension no native AWS feature surfaces
today.

### `finops/model-cascade-router/`

The highest-leverage optimization: routes each prompt to the cheapest model
that meets the quality bar, with an eval harness that proves quality held after
routing changes.

### `finops/token-budget-circuit-breaker/`

Turns FinOps from monitoring into enforcement: blocks or throttles workloads
that breach their token/cost budget policy before the bill arrives.

## Default region

All projects default to **us-east-1**; every deploy script and CLI honors
`AWS_REGION=…` to override.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| tls-load-balancer | Application Load Balancer | $0.0225 | $0.0225 | $0.0225/hr base + $0.008/LCU-hr (typically 1 LCU at light load). |
| tls-load-balancer | ACM certificate | $0.0000 | $0.0000 | Public certs are free. |
| tls-load-balancer | Route 53 hosted zone | $0.0007 | $0.0007 | $0.50/zone/month ≈ $0.0007/hr; queries $0.40/M (negligible at demo load). |
| encryption-kms | KMS CMK | $0.0014 | $0.0014 | $1/key/month. S3 storage + KMS request charges pay-per-use. |
| fine-grained-access | (none) | $0.0000 | $0.0001 | SQL/scripts only — runs against your existing Redshift/Lake Formation. |
| mcp-oauth-reference-server | Cognito user pool | $0.0000 | $0.0000 | Free up to 50k MAU; ~$0/hr at demo scale. |
| sigv4-mcp-bridge | Lambda + Function URL | $0.0000 | $0.0001 | Pay-per-invoke, no idle cost. |
| guardrails-from-policy | Bedrock Guardrail | $0.0000 | $0.0010 | $0.75/1M text units when ApplyGuardrail is called; no idle cost. |
| prompt-injection-test-harness | Bedrock InvokeModel | $0.0000 | $0.0050 | Test harness only — costs scale with the 210-payload sweep when you run it. |
| bedrock-audit-trail | KMS CMK × 2 | $0.0028 | $0.0028 | $1/key/month. |
| bedrock-audit-trail | Kinesis Firehose | $0.0000 | $0.0010 | $0.029/GB ingested; pay-per-GB only. |
| bedrock-audit-trail | Lambda + Glue Catalog + Athena + S3 | $0.0000 | $0.0010 | All pay-per-use. |
| config-rules-for-genai | 9 Config rule Lambdas | $0.0000 | $0.0010 | $0.001 per rule evaluation. AWS Config recorder bills outside this stack. |
| finops/ai-finops-data-lake | KMS CMK + S3 + Glue + Athena | $0.0014 | $0.0024 | $0.0014/hr fixed (KMS); jobs and queries pay-per-use. |
| finops/agent-run-cost-tracer | KMS CMK + Firehose + S3 | $0.0014 | $0.0019 | KMS fixed; Firehose pay-per-GB. |
| finops/model-cascade-router | KMS CMK + S3 + IAM | $0.0014 | $0.0024 | Bedrock evaluation jobs are on-demand, ~$0.10/job. |
| finops/token-budget-circuit-breaker | DynamoDB + SNS + Lambda | $0.0000 | $0.0010 | DynamoDB on-demand + PITR; ~$0/hr at rest. |
| **Chapter total** | | **~$0.0316** | **~$0.0442** | ALB + 4 KMS keys dominate; everything else is pay-per-use. |

The chapter's standing cost is dominated by the **ALB** ($0.0225/hr) and
**4 KMS customer-managed keys** ($0.0014/hr each = $0.0056/hr). Tear down
the ALB stack and delete the keys you don't need between sessions to drop
the chapter to ~$0/hr at rest.

