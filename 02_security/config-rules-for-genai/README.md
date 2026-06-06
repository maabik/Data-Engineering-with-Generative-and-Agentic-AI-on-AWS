# Config Rules for GenAI

Custom AWS Config rules that surface GenAI and MCP-specific security risks as
continuous compliance findings. Each rule is a standalone Python Lambda handler
built on a shared evaluator library. All rules ship with complete unit tests
— no AWS credentials required to run them.

---

## Rules

| Rule name (Config) | Resource type | What it checks |
|---|---|---|
| `genai-bedrock-invocation-logging-enabled` | `AWS::Bedrock::ModelInvocationLoggingConfiguration` | Bedrock model invocation logging is enabled with ≥1 destination (CloudWatch or S3) |
| `genai-bedrock-guardrail-attached` | `AWS::Bedrock::Agent` | Every Bedrock Agent has a Guardrail attached |
| `genai-bedrock-kb-encryption-cmk` | `AWS::Bedrock::KnowledgeBase` | Knowledge Bases are encrypted with a CMK (not AWS-managed key) |
| `genai-lambda-url-auth-required` | `AWS::Lambda::Url` | Lambda Function URLs on AI-workload functions use `AuthType=AWS_IAM` |
| `genai-kms-cmk-rotation-ai-workloads` | `AWS::KMS::Key` | AI-workload CMKs have rotation enabled and are single-region |
| `genai-bedrock-agent-least-privilege` | `AWS::Bedrock::Agent` | Agent execution roles don't carry Admin/PowerUser/FullAccess patterns |
| `genai-s3-ai-data-no-public-access` | `AWS::S3::Bucket` | AI data buckets have all four Block Public Access flags enabled |
| `genai-cloudtrail-bedrock-data-events` | `AWS::CloudTrail::Trail` | A trail logs Bedrock data events (InvokeModel, InvokeAgent) |
| `genai-mcp-server-auth-required` | `AWS::Lambda::Function` | MCP server Lambdas use OAuth 2.1 (`MCP_AUTH_TYPE=oauth`) or SigV4 (Function URL `AuthType=AWS_IAM`) |

---

## Quick start

```bash
cd 02_security/config-rules-for-genai

# Run all tests (no AWS creds)
pip install -e ".[dev]"
pytest

# Deploy to AWS
./infra/deploy.sh --region us-west-2 --prefix genai
```

---

## Architecture

```
config-rules-for-genai/
├── lib/
│   └── evaluator.py      ← shared: event parsing, put_evaluations, batching
├── rules/
│   ├── bedrock_invocation_logging_enabled.py
│   ├── bedrock_guardrail_attached.py
│   ├── bedrock_kb_encryption_cmk.py
│   ├── lambda_url_auth_required.py
│   ├── kms_cmk_rotation_ai_workloads.py
│   ├── bedrock_agent_least_privilege.py
│   ├── s3_ai_data_no_public_access.py
│   ├── cloudtrail_bedrock_data_events.py
│   ├── bedrock_model_access_tag_required.py
│   └── mcp_server_auth_required.py
├── infra/
│   ├── config_rules.yaml   ← SAM template: 9 Lambdas + Config rules + IAM role
│   └── deploy.sh
└── tests/
    ├── helpers.py           ← FakeConfigClient, make_event()
    ├── test_evaluator.py    ← 17 tests for shared evaluator
    └── test_rules.py        ← 70 tests for all 9 rules
```

Each rule file follows the same pattern:

```python
def evaluate(config_item: dict, rule_params: dict) -> tuple[str, str]:
    # Returns (Status.COMPLIANT | Status.NON_COMPLIANT | Status.NOT_APPLICABLE, annotation)
    ...

def lambda_handler(event: dict, context) -> list[dict]:
    return Evaluator(event, context).run(
        evaluate,
        applicable_resource_types=["AWS::Bedrock::Agent"],
    )
```

---

## Tagging and scope

Most rules are scoped to tagged resources to avoid false positives on unrelated
workloads. Default tag keys:

| Rule | Default tag key | Default tag values in scope |
|---|---|---|
| Lambda URL auth | `Workload` | `MCP`, `GenAI`, `Bedrock`, `AI` |
| KMS rotation | `Workload` | `AI`, `GenAI`, `Bedrock`, `MCP` |
| S3 public access | `DataClassification` | `AI`, `AIData`, `Confidential`, `Restricted`, `GenAI` |
| MCP server auth | `MCPServer` | `true` |

Override via `InputParameters` in the CloudFormation template or
`rule_params` in your tests.

---

## Rule parameters

Each rule accepts optional `InputParameters` (CloudFormation) / `rule_params` (tests):

**`bedrock-kb-encryption-cmk`**
- `required_key_prefix` — KMS key ARN must start with this string (e.g. `arn:aws:kms:us-west-2:123456789012:key/`)

**`lambda-url-auth-required`**
- `tag_key` — tag key to scope evaluation (default: `Workload`)
- `tag_value` — specific tag value to match; empty = match any AI workload value

**`kms-cmk-rotation-ai-workloads`**
- `tag_key`, `tag_value` — scope filter
- `allow_multi_region` — `"true"` to suppress the multi-region finding

**`bedrock-agent-least-privilege`**
- `disallowed_role_patterns` — comma-separated role name fragments to flag (default: `Admin,PowerUser,FullAccess,AdministratorAccess`)

**`s3-ai-data-no-public-access`**
- `tag_key`, `tag_value` — scope filter; set both to `""` to evaluate all S3 buckets

**`bedrock-model-access-tag-required`**
- `required_tags` — comma-separated tag keys (default: `DataClassification,Team,Environment`)

**`mcp-server-auth-required`**
- `tag_key`, `tag_value` — identify MCP functions (default: `MCPServer=true`)

---

## Deploy

```bash
./infra/deploy.sh [--region us-west-2] [--prefix genai] [--bucket my-staging-bucket]
```

The script:
1. Creates a versioned staging S3 bucket (idempotent).
2. Zips `lib/` + `rules/` → `build/rules.zip`.
3. Uploads the zip.
4. Deploys the SAM/CloudFormation stack with `--capabilities CAPABILITY_IAM`.
5. Prints a table of active rules.

**IAM required to deploy:**
`cloudformation:*`, `lambda:*`, `iam:CreateRole`, `iam:AttachRolePolicy`,
`config:PutConfigRule`, `s3:PutObject`, `s3:CreateBucket`.

**IAM required at runtime (the ConfigRuleRole):**
`config:PutEvaluations` + `logs:CreateLogGroup` + `logs:PutLogEvents`.

---

## Teardown

```bash
REGION=${AWS_REGION:-us-east-1}
PREFIX=genai
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${PREFIX}-config-rules-${ACCOUNT}-${REGION}"

# 1. Delete the CloudFormation stack — removes the Lambda functions,
#    the ConfigRuleRole, and all Config rules created by deploy.sh.
aws cloudformation delete-stack --stack-name genai-config-rules --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name genai-config-rules --region "$REGION"

# 2. Empty + delete the staging bucket (versioned — drop all versions
#    and delete-markers, then the bucket).
aws s3api delete-objects --bucket "$BUCKET" \
    --delete "$(aws s3api list-object-versions --bucket "$BUCKET" \
        --query='{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
        --output json)" 2>/dev/null || true
aws s3api delete-objects --bucket "$BUCKET" \
    --delete "$(aws s3api list-object-versions --bucket "$BUCKET" \
        --query='{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' \
        --output json)" 2>/dev/null || true
aws s3 rb "s3://${BUCKET}" --region "$REGION"

# 3. Local build artefacts.
rm -rf build/
```

**Why this matters:** AWS Config bills per evaluation triggered, and a
Config rule that fires on `AWS::Lambda::Function` configuration changes
in a busy account can rack up hundreds of evaluations per day even when
"nothing is happening". Deleting the stack stops the meter immediately.

**Note on the AWS Config recorder.** `deploy.sh` *does not* enable the
Config recorder — it assumes you already have one running. If you
enabled the recorder solely for this demo, also turn it off:

```bash
aws configservice stop-configuration-recorder \
    --configuration-recorder-name default --region "$REGION"
```

---

## Testing without AWS

```bash
pytest                    # all 87 tests
pytest tests/test_rules.py -k bedrock   # filter by name
pytest -v --tb=short      # verbose with short tracebacks
```

The `FakeConfigClient` in `tests/helpers.py` captures `put_evaluations` calls
in memory. You can write new rule tests without any mocking framework:

```python
from tests.helpers import FakeConfigClient, make_event
from rules.my_new_rule import lambda_handler

def test_my_rule():
    event = make_event(
        resource_type="AWS::Bedrock::Agent",
        resource_id="agent-123",
        configuration={"agentName": "test", "guardrailConfiguration": {}},
    )
    fake = FakeConfigClient()
    from lib.evaluator import Evaluator
    Evaluator(event, None).run(
        lambda ci, p: ("NON_COMPLIANT", "no guardrail"),
        config_client=fake,
    )
    assert fake.compliance_types() == ["NON_COMPLIANT"]
```
