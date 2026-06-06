# step-function-generator

S3-triggered AWS Lambda that turns a natural-language prompt into an AWS Step
Functions state machine. The prompt is sent to Amazon Bedrock (Claude Messages
API), the model output is validated as Amazon States Language (ASL) JSON, the
definition is written back to S3, and a state machine is created or updated.

```
 prompts/*.txt          ┌──────────────┐   InvokeModel    ┌─────────────────┐
 ───────────► S3 ─────► │  Lambda      │ ───────────────► │ Bedrock (Claude)│
                        │  (validator) │ ◄─────────────── │  Messages API   │
                        └──────┬───────┘     ASL JSON     └─────────────────┘
                               │
                               │ validated definition
                               ▼
                  generated/*.json + StepFunctions state machine
```

## What was fixed vs. the original `step-function-lambda.py`

The original script in this folder is the chapter sample. Issues addressed:

| Category | Original | Now |
|---|---|---|
| Bedrock client | `boto3.client('bedrock')` (control plane) | `bedrock-runtime` |
| Request schema | Legacy `prompt` + `max_tokens_to_sample` | Messages API (`anthropic_version`, `messages`, `system`) |
| Response parse | `response_body.get("completion")` | `content[0].text` |
| Missing imports | `time` referenced but not imported | dropped the manual `put_log_events`; `logger` writes to CloudWatch |
| `invoke_model` | Called as a free function | Called on the runtime client |
| State-machine name | Hardcoded `GlueJobOrchestration` (collides) | Derived from the prompt key; `update_state_machine` on conflict |
| Role ARN | `[account_id]` placeholder | Env var `STATE_MACHINE_ROLE_ARN` |
| Recursion | Lambda wrote back to the same bucket without filter | `INPUT_PREFIX` guard inside Lambda + S3 prefix filter on the trigger |
| Output validation | Raw model text deployed | JSON parse + structural check + `Resource` allowlist |
| S3 encryption | Default | `ServerSideEncryption=AES256` |
| Logging | Manual `put_log_events` (needs sequence token) | Standard `logging` to CloudWatch |
| Retries | None | `botocore` standard retry mode |

### Security posture

* **Prompt injection containment.** The model output is parsed as JSON and
  every `Task.Resource` is checked against
  `ALLOWED_TASK_RESOURCE_PREFIXES` (defaults: Glue, Lambda, AWS-SDK service
  integrations). Hallucinated or attacker-supplied HTTP/email/external ARNs
  are rejected before `CreateStateMachine`.
* **Recursion guard.** The Lambda only processes keys under `INPUT_PREFIX`
  (`prompts/`); generated definitions are written under `OUTPUT_PREFIX`
  (`generated/`). The S3 notification also filters by `prompts/`.
* **Least-privilege IAM.** `iam:PassRole` is scoped to the state-machine
  role with `iam:PassedToService = states.amazonaws.com`. State-machine
  actions are scoped to `${PREFIX}*` names.
* **Bucket hardening.** Public access blocked, default SSE-S3.

## Deploy / test / teardown

Prereqs: AWS credentials with admin-equivalent rights, Bedrock model access
to a Claude inference profile in your region.

```bash
./deploy.sh                           # creates bucket, IAM, Lambda, S3 trigger
aws s3 cp sample-prompt.txt \
  s3://sfn-gen-${ACCOUNT}-${REGION}/prompts/demo.txt
aws logs tail /aws/lambda/sfn-gen-fn --follow
aws stepfunctions describe-state-machine \
  --state-machine-arn arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:sfn-gen-demo \
  --query definition --output text | jq .
./teardown.sh                         # remove everything
```

Override defaults via env: `AWS_REGION`, `PREFIX`, `BUCKET`, `MODEL_ID`.

Default model: `us.anthropic.claude-haiku-4-5-20251001-v1:0`.

## Files

- `step-function-lambda.py` — Lambda handler.
- `deploy.sh` — idempotent deploy of bucket, IAM, Lambda, S3 trigger.
- `teardown.sh` — removes state machines, bucket, Lambda, log group, IAM.
- `sample-prompt.txt` — example prompt used in the live test.
