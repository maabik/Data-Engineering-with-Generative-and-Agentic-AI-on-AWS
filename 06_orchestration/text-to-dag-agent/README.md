# text-to-dag-agent

Strands agent on **Bedrock AgentCore Runtime** that turns natural-language
pipeline descriptions into deployable orchestration. Tools live in a single
Lambda exposed as MCP via **AgentCore Gateway**, so the agent and the tools
scale and deploy independently.

```
                                     Cognito JWT
client (invoke_agent.py)  ─────────────────────────►  AgentCore Runtime
                                                        │   (Strands agent
                                                        │    container)
                                                        │
                                                MCP-over-HTTP, JWT
                                                        ▼
                                       AgentCore Gateway (text-to-dag-agent-gw)
                                                        │
                                       Lambda invoke (GATEWAY_IAM_ROLE)
                                                        ▼
                                       ┌───────────────────────────────┐
                                       │  text-to-dag-agent-tools      │
                                       │  (Lambda)                     │
                                       │  • generate_asl               │
                                       │  • generate_airflow_dag       │
                                       │  • generate_iam_policy        │
                                       │  • validate_asl               │
                                       │  • validate_airflow_dag       │
                                       │  • deploy_sfn                 │
                                       └──────────────┬────────────────┘
                                                      │ Bedrock InvokeModel
                                                      │ states:Create/UpdateStateMachine
                                                      ▼
                                          Step Functions state machines
```

## Why this shape

The brief is "agent that emits orchestration." Pure prompt-engineering produces
plausible JSON; an agent loop with tools produces *deployable* JSON because the
model can call `validate_asl` and self-correct. Putting the tools behind
AgentCore Gateway means:

- The Lambda owns Bedrock + Step Functions perms; the runtime container only
  needs MCP/Cognito/Bedrock-for-the-LLM perms.
- New tools are added by editing `tools/lambda_function.py` and the
  `toolSchema` in `deploy.sh` — no agent rebuild.
- Cross-agent reuse: any other Strands or Bedrock Agent in the account can
  attach the same Gateway and gets the same tool set with the same auth model.

## Tools

| Tool                   | Input                    | Output                                       |
| ---------------------- | ------------------------ | -------------------------------------------- |
| `generate_asl`         | `description`            | `{definition: <ASL JSON>}`                   |
| `generate_airflow_dag` | `description`            | `{dag_python: <source>}`                     |
| `generate_iam_policy`  | `definition` (ASL JSON)  | `{policy: <IAM policy>}`                     |
| `validate_asl`         | `definition`             | `{ok, errors, diagnostics}`                  |
| `validate_airflow_dag` | `dag_python`             | `{ok, errors, warnings, info}`               |
| `deploy_sfn`           | `name`, `definition`     | `{ok, stateMachineArn}`                      |

**ASL validation** layers three checks: structural (top-level `StartAt` +
`States`, every Task has a `Type`, `StartAt` references a defined state),
Resource-allowlist (`arn:aws:states:::glue:`, `:::lambda:`, `:::aws-sdk:`),
and AWS-side `ValidateStateMachineDefinition` lint, which returns codes
like `SCHEMA_VALIDATION_FAILED` and `MISSING_TRANSITION_TARGET` with source
locations. Any error blocks `deploy_sfn`.

**Airflow DAG validation** is AST-based: parses the source, requires an
`airflow` import, requires `DAG(...)` or `@dag` usage, validates `dag_id`
as a valid identifier, flags duplicate `task_id` values, warns on
`catchup=True`, and extracts `dag_id`, `schedule`, operator class names,
and `task_id`s into `info` for downstream consumers.

## Deploy / test / teardown

Prereqs:

- AWS creds with admin-equivalent rights (the deploy creates IAM roles,
  Cognito, Lambda, AgentCore Gateway+Runtime, ECR).
- Bedrock model access for an Anthropic inference profile in your region.
  Default: `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
- Either Docker or Finch installed locally for the AgentCore Runtime image
  build (must build `linux/arm64`).

```bash
./deploy.sh

# Quick test:
python -m venv .venv && source .venv/bin/activate
pip install boto3 requests
RUNTIME_ARN=<from deploy output> SECRET_NAME=text-to-dag-agent/gateway-credentials \
    AWS_REGION=us-east-1 \
    python scripts/invoke_agent.py "Build a Glue job orchestration that runs RawIngest, then Transform, then Publish, retrying transient failures. Deploy as 'demo-pipeline'."

./teardown.sh
```

Override defaults via env: `AWS_REGION`, `PREFIX`, `MODEL_ID`,
`RUNTIME_PASSWORD`.

## Live test results (us-west-2)

### 1. Step Functions pipeline (`examples/demo-pipeline.asl.json`)

Prompt: *"Build a Glue job orchestration that runs RawIngest, then Transform,
then Publish, retrying transient failures. Validate it and deploy as
'demo-pipeline'."*

Agent loop:

1. **`generate_asl`** produced a 7-state pipeline (3 Task + 1 Succeed + 3 Fail
   states with per-step `Catch` blocks).
2. **`validate_asl`** returned `{ok: true}` and zero diagnostics.
3. **`generate_iam_policy`** emitted a policy granting only
   `glue:StartJobRun`, `glue:GetJobRun`, `glue:GetJobRuns`,
   `glue:BatchStopJobRun` (saved to `examples/demo-pipeline.iam.json`).
4. **`deploy_sfn`** created
   `arn:aws:states:us-west-2:123456789012:stateMachine:text-to-dag-agent-demo-pipeline`
   (`describe-state-machine` -> `status: ACTIVE`).

End-to-end execution (input pointing at three real Glue Python-shell jobs):
the SFN ran for ~10 minutes through 3 retry attempts on `RawIngest`
(`MaxAttempts: 3` honored exactly), then the `Catch` block routed to the
`PublishError`/`RawIngestError` fail states as designed — the agent's retry
+ catch logic worked end-to-end on AWS.

### 2. Airflow DAG (`examples/simple_ingest.py`)

Prompt: *"Generate an Apache Airflow DAG that runs the Glue job 'raw-ingest'
once a day at midnight UTC. dag_id should be 'simple_ingest'. Validate the
DAG and report the result."*

Agent loop:

1. **`generate_airflow_dag`** produced a `@dag`-decorated source using
   `AwsGlueJobOperator`.
2. **`validate_airflow_dag`** returned
   `{ok: true, info: {dag_id: 'simple_ingest', schedule: '0 0 * * *',
   operators: ['AwsGlueJobOperator'], task_ids: ['run_raw_ingest']}}`.

### 3. Canonical Chapter 6 graph (`examples/glue-workflow.json`, `examples/glue_workflow_dag.py`)

The book references a specific 4-Glue-job graph on pp. 264 and 270:

```
glue-job-extract1 ──┐
                    ├─► glue-job-transform ─► glue-job-validate ─► glue-job-publish
glue-job-extract2 ──┘
```

Failures route to the SNS topic `glue-workflow-error`. Both shapes are
checked in:

- `examples/glue-workflow.json` — Step Functions ASL (`Parallel` over the
  two extracts, per-step `Retry` + `Catch` to a single `NotifyFailure ->
  FailState` path that publishes to SNS via `arn:aws:states:::sns:publish`).
  Pass the topic ARN as `errorTopicArn` in the execution input.
- `examples/glue_workflow_dag.py` — Airflow DAG with the same graph
  (`[extract1, extract2] >> transform >> validate >> publish`) and an
  `on_failure_callback` that publishes to the SNS topic ARN read from the
  Airflow Variable `glue_workflow_error_topic_arn`.

### 4. Validator catches deliberately broken inputs

- ASL with a non-existent `StartAt` and a Glue Task missing `Parameters`
  drew AWS lint codes `MISSING_TRANSITION_TARGET` and
  `SCHEMA_VALIDATION_FAILED` with source locations.
- DAG with `dag_id="my dag"` and `catchup=True` produced
  `errors: ["dag_id 'my dag' must be a valid identifier"]` and
  `warnings: ["catchup=True will backfill on first deploy"]`.

## Files

- `tools/lambda_function.py` — six MCP tools behind one Lambda; routes via
  `context.client_context.custom["bedrockAgentCoreToolName"]` (the Gateway
  contract).
- `agent/text_to_dag_agent.py` — Strands agent + AgentCore Runtime entrypoint
  that wraps each tool as a `@tool` calling MCP-over-HTTP.
- `agent/Dockerfile`, `agent/requirements.txt` — `linux/arm64` container for
  AgentCore Runtime.
- `scripts/invoke_agent.py` — minimal client that mints a Cognito token and
  POSTs to the runtime endpoint.
- `examples/` — artifacts from live agent runs: deployed ASL definition,
  generated Airflow DAG, generated IAM policy.
- `deploy.sh` / `teardown.sh` — idempotent infra scripts.

## Security notes

- **All tool perms live in the Lambda role**, not the agent runtime role. The
  runtime can only invoke Bedrock for the LLM and call the Gateway via JWT.
- **Validation gate.** `deploy_sfn` re-runs `validate_asl` server-side, so
  even if the model output skips the validation step the deployed definition
  still has to pass the allowlist.
- **Cognito client-credentials stored in Secrets Manager**, not env vars.
- **`iam:PassRole` is conditioned** on `iam:PassedToService =
  states.amazonaws.com` and the Step Functions execution role is name-prefixed
  to `${PREFIX}*`.
- **Recursion isn't possible** — there's no S3 trigger; the runtime is invoked
  directly by an authenticated client.

## Known caveats

- Container build needs `linux/arm64` regardless of host CPU. `deploy.sh`
  detects Docker (`buildx`) or Finch (`buildctl`).
- The agent occasionally regenerates a definition once when the first pass
  has retry rules outside the allowlist — that's the loop earning its keep.
- `update-agent-runtime` does **not** auto-pick up a re-pushed image with the
  same tag; the script always sends a fresh `containerUri`, but if you
  iterate by hand, push a new tag.
