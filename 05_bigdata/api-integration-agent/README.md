# api-integration-agent

Strands agent on **Bedrock AgentCore Runtime** that takes a target
dataset (and either a Secrets Manager record or an inline `api_spec`)
and emits, deploys, schedules, and live-tests a Lambda that pulls
data from the API and lands it in the data lake's landing zone.

The six MCP tools (`list_api_secrets`, `read_api_secret`,
`write_lambda_code`, `deploy_lambda`, `invoke_lambda_test`,
`write_integration_report`) live behind one Lambda exposed via
**AgentCore Gateway**.

```
   request JSON (direct invoke OR drop into                Cognito JWT
   s3://.../api-requests/<file>.json)                              │
            │                                                      ▼
            ▼                                              AgentCore Runtime  (api_integration_agent)
   EventBridge ──► api-integration-agent-bridge ─────────►          Strands agent container
                                                                   │
                                                          MCP-over-HTTP, JWT
                                                                   ▼
                                                   AgentCore Gateway (api-integration-agent-gw)
                                                                   │
                                                   Lambda invoke (GATEWAY_IAM_ROLE)
                                                                   ▼
                                       ┌─────────────────────────────────────────┐
                                       │  api-integration-agent-tools (Lambda)                     │
                                       │  • list_api_secrets                     │
                                       │  • read_api_secret  (creds redacted)    │
                                       │  • write_lambda_code                    │
                                       │  • deploy_lambda    (Lambda + EB rule)  │
                                       │  • invoke_lambda_test                   │
                                       │  • write_integration_report             │
                                       └─────────────────┬───────────────────────┘
                                                         │
                                                         │  creates / updates
                                                         ▼
                                       ┌──────────────────────────────────────┐
                                       │  api-integration-agent-ing-<dataset>          │
                                       │  + EventBridge schedule rule         │
                                       │      writes -> s3://${BUCKET}/       │
                                       │      landing/<dataset>/.../*.json    │
                                       └──────────────────────────────────────┘
```

## Two ingestion paths

1. **Public APIs (no auth).** Pass an inline `api_spec` with
   `auth_type: "none"`; the generated Lambda performs an unauthenticated
   GET. No Secrets Manager record is needed and the deployed Lambda has
   *no* `secretsmanager:GetSecretValue` invocations on the hot path.
2. **Authenticated APIs.** Pre-load a Secrets Manager record under
   `api-integration-agent/apis/<name>` with at minimum `endpoint`, `auth_type`
   (`bearer` | `api_key_header` | `api_key_query` | `basic`), and the
   credential field (`api_key`/`token`/`password`). Pass `secret_name`
   in the request; the agent inspects the record (credential is
   *redacted* in transit), writes ingestion code that fetches the
   credential at runtime via boto3, then deploys + schedules.

In both cases the agent's six-step workflow is the same:

| Step | Tool                       | Purpose                                                 |
| ---- | -------------------------- | ------------------------------------------------------- |
| 1    | `list_api_secrets`/`read_api_secret` | Discover endpoint + auth shape (auth path) |
| 2    | `write_lambda_code`        | Persist generated Python source to S3                   |
| 3    | `deploy_lambda`            | Create/update Lambda + EventBridge schedule rule        |
| 4    | `invoke_lambda_test`       | Sync invoke + list `landing/<dataset>/` to confirm      |
| 5    | `write_integration_report` | Persist final summary JSON                              |
| 6    | (final message)            | Return same JSON to the caller                          |

## Tools

| Tool                       | Input                                                                              | Output                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `list_api_secrets`         | (none)                                                                             | `{ok, prefix, secrets:[{name, description, lastChanged, tags}], count}`             |
| `read_api_secret`          | `secret_name`                                                                      | `{ok, secretName, fields (creds redacted), hasCredential, credentialKey}`           |
| `write_lambda_code`        | `dataset, code, requirements?`                                                     | `{ok, dataset, codeUri, requirementsUri?}`                                          |
| `deploy_lambda`            | `dataset, code_uri, secret_name?, schedule, env?`                                  | `{ok, dataset, functionName, functionArn, ruleName, ruleArn, schedule, envKeys}`    |
| `invoke_lambda_test`       | `dataset`                                                                          | `{ok, functionName, statusCode, functionError, responsePayload, landingPrefix, objectsLanded[], objectCount}` |
| `write_integration_report` | `dataset, report`                                                                  | `{ok, reportUri}`                                                                   |

`read_api_secret` strips raw credential values before returning to the
agent; only their *presence* is reported. The deployed Lambda reads the
credential directly at runtime, so the agent never has to handle the
secret material itself.

`deploy_lambda` is namespaced to `api-integration-agent-ing-*` for both the Lambda
function name and the EventBridge rule name, and the tools-Lambda IAM
policy is scoped to that prefix. The agent cannot create or modify
Lambdas / rules outside its own namespace.

## Integration report contract

The agent's final output (and the object written to
`s3://${BUCKET}/api-integrations/reports/<dataset>.json`) is a single
JSON object:

```json
{
  "dataset": "jph-todos",
  "source": {
    "endpoint": "https://jsonplaceholder.typicode.com/todos",
    "authType": "none"
  },
  "lambda": {
    "functionName": "api-integration-agent-ing-jph-todos",
    "functionArn": "arn:aws:lambda:us-west-2:...:function:api-integration-agent-ing-jph-todos"
  },
  "schedule": {
    "ruleName": "api-integration-agent-ing-jph-todos",
    "ruleArn":  "arn:aws:events:us-west-2:...:rule/api-integration-agent-ing-jph-todos",
    "expression": "rate(1 hour)"
  },
  "landing": {
    "bucket": "data-profiling-agent-...-us-west-2",
    "prefix": "landing/jph-todos/",
    "sampleKey": "landing/jph-todos/ingest_date=2026-05-19/hour=21/...json",
    "objectCount": 1
  },
  "testRun": { "statusCode": 200, "ok": true, "responseBody": "{...}" },
  "codeUri":  "s3://.../api-integrations/code/jph-todos/lambda_function.py",
  "reportUri":"s3://.../api-integrations/reports/jph-todos.json",
  "status": "active",
  "notes": "Public API, no authentication. 200 todos fetched."
}
```

`status` is `active` (deployed + smoke test passed), `deployed-untested`
(Lambda created but the test invocation failed), or `failed` (deploy
itself errored).

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with
buildx) or `finch` for `linux/arm64` builds (AgentCore Runtime requires
arm64). The shared data lake bucket
(default `data-profiling-agent-${ACCOUNT}-${REGION}`) must already exist — this project
uses it for `landing/`, `api-integrations/code/`,
`api-integrations/reports/`, and `api-requests/`.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=api-integration-agent`,
`DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`,
`API_SECRET_PREFIX=api-integration-agent/apis/`,
`INGESTION_FN_PREFIX=api-integration-agent-ing-`,
`INGESTION_RULE_PREFIX=api-integration-agent-ing-`. The deploy fails fast if the data
bucket is missing. Idempotent — re-runs update everything in place.

The deploy creates: five IAM roles (lambda, bridge, gateway, runtime,
**ingestion**), a Cognito user pool / resource server / app client /
test user, a Secrets Manager secret for the runtime, the tools Lambda,
an AgentCore Gateway with a Lambda target and the inline tool schema,
an ECR repo + the agent container image, the AgentCore Runtime, the
bridge Lambda, and an EventBridge rule that fires on
`s3:ObjectCreated` under `api-requests/`.

## Live test — public API

The simplest demo uses `https://jsonplaceholder.typicode.com/todos`,
which needs no authentication:

```
cat > /tmp/req.json <<'JSON'
{
  "dataset": "jph-todos",
  "description": "Fetch JSON Placeholder todos and land them as JSON",
  "api_spec": {
    "endpoint": "https://jsonplaceholder.typicode.com/todos",
    "auth_type": "none"
  },
  "schedule": "rate(1 hour)"
}
JSON

RUNTIME_ARN=<from deploy output> SECRET_NAME=api-integration-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py /tmp/req.json
```

Or drop the request directly in the bucket and let EventBridge fire
the bridge Lambda:

```
aws s3 cp /tmp/req.json s3://data-profiling-agent-${ACCOUNT}-${REGION}/api-requests/jph-todos.json
```

Either path produces `aws lambda list-functions | grep api-integration-agent-ing-jph-todos`
and `aws s3 ls s3://${BUCKET}/landing/jph-todos/` populated within
~1-3 minutes.

## Live test — authenticated API

```
# 1. Pre-load the API record
aws secretsmanager create-secret --name api-integration-agent/apis/datausa-population \
  --secret-string '{
    "endpoint":"https://datausa.io/api/data",
    "auth_type":"none",
    "default_query":"drilldowns=Nation&measures=Population",
    "dataset_hint":"US population by year"
  }' --region us-west-2

# 2. Invoke the agent — it'll discover and use the record
cat > /tmp/req.json <<'JSON'
{ "dataset": "datausa-population", "secret_name": "api-integration-agent/apis/datausa-population",
  "schedule": "rate(6 hours)" }
JSON
RUNTIME_ARN=<...> SECRET_NAME=api-integration-agent/gateway-credentials AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py /tmp/req.json
```

For a record with real auth, add `api_key` (or `token`/`password`) to
the secret; only field-presence is exposed to the agent and the
deployed Lambda fetches the value at runtime.

## Layout

```
api-integration-agent/
├── deploy.sh                       # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py        # MCP tools target
├── agent/
│   ├── api_integration_agent.py    # Strands @tool wrappers + entrypoint
│   ├── Dockerfile                  # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py       # api-requests/ + direct -> Runtime
└── scripts/invoke_agent.py         # manual invocation helper
```

## Teardown

```
./teardown.sh
```

Deletes everything api-integration-agent created at deploy time — EventBridge request
rule, both Lambdas, the AgentCore Runtime, the Gateway and target, the
ECR repo, the Cognito pool, the Secrets Manager secret, and all five
IAM roles. The shared S3 bucket and any user-loaded `api-integration-agent/apis/*`
secrets are left intact. Set `DELETE_AIA_DATA=1` to also empty the
`api-integrations/` and `api-requests/` prefixes AND remove every
`api-integration-agent-ing-*` ingestion Lambda + its EventBridge schedule rule that the
agent has materialized.
