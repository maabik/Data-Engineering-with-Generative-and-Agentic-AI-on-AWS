# Guardrails from Policy

A Bedrock-powered tool that ingests a corporate data-classification or Acceptable Use Policy (AUP) document — PDF, DOCX, or plain text — and generates a deployable Amazon Bedrock Guardrails configuration. Demonstrates the **policy-as-code-via-LLM** pattern: the model reads your policy prose and produces a machine-enforceable guardrail.

---

## How it works

```
policy.pdf / .docx / .txt
        │
        ▼
  extractor.py          ← pypdf / python-docx / plain read
        │ raw text
        ▼
  policy_parser.py      ← Claude via Bedrock Converse
        │ ParsedPolicy dataclass
        ▼
  guardrail_builder.py  ← deterministic mapping to boto3 shapes
        │ GuardrailConfig
        ▼
  deployer.py           ← bedrock.CreateGuardrail / UpdateGuardrail
        │
        ▼
  Bedrock Guardrail (ARN + version)
```

Claude extracts:

| Field | How it's used |
|---|---|
| Data classification tiers | Drives blocked-content messaging severity |
| Denied topics | `topicPolicyConfig` — topics the model must refuse |
| Blocked phrases / codewords | `wordPolicyConfig` |
| PII entity types | `sensitiveInformationPolicyConfig` with ANONYMIZE or BLOCK |
| Content filter strengths | `contentPolicyConfig` — hate, sexual, violence, misconduct, prompt-attack |
| Grounding / relevance thresholds | `contextualGroundingPolicyConfig` |

---

## Quick start

```bash
cd 02_security/guardrails-from-policy
pip install -e ".[dev]"

# Dry-run against the bundled sample policy (uses Bedrock Converse — needs AWS creds)
policy2guardrail examples/sample_policy.txt --dry-run --verbose

# Parse only — print structured JSON, no Bedrock Guardrails call
policy2guardrail examples/sample_policy.txt --parse-only

# Deploy (create or update)
policy2guardrail my_policy.pdf \
  --region us-west-2 \
  --kms-key-id arn:aws:kms:us-west-2:123456789012:key/mrk-abc \
  --tag Environment=prod \
  --tag Team=security \
  --json-out guardrail_config.json

# Unit tests (no AWS creds needed)
pytest
```

---

## CLI reference

```
policy2guardrail <policy_file> [options]

Positional:
  policy_file          PDF, DOCX, or TXT policy document

Options:
  --region             AWS region (default: us-east-1)
  --model-id           Bedrock model for extraction (default: claude-haiku-4-5)
  --dry-run            Print the API config without deploying
  --parse-only         Stop after parsing; print structured JSON
  --json-out PATH      Write final Bedrock config JSON to file
  --kms-key-id KEY     KMS key ARN for guardrail encryption at rest
  --tag KEY=VALUE      Resource tag (repeat for multiple)
  --name NAME          Override the guardrail name
  --verbose / -v       Print extracted fields before building config
```

---

## Python API

```python
from guardrails_from_policy import (
    extract_text,
    PolicyParser,
    build_guardrail_config,
    deploy_guardrail,
)

# 1. Extract text from any supported format
text = extract_text("my_policy.pdf")

# 2. Parse with Claude
policy = PolicyParser(region="us-west-2").parse(text)
print(policy.denied_topics)       # list[DeniedTopic]
print(policy.pii_entities)        # list[PiiEntity]
print(policy.grounding_threshold) # float | None

# 3. Build the boto3-ready config
config = build_guardrail_config(policy)
print(config.as_dict())           # JSON-serialisable dict

# 4. Deploy (create or update)
result = deploy_guardrail(
    config,
    region="us-west-2",
    kms_key_id="arn:aws:kms:...",
    tags={"CreatedBy": "guardrails-from-policy"},
)
print(result.guardrail_arn, result.version)
```

### Using a CallableTarget or custom client

The parser accepts any object with a `.converse(**kwargs)` method, so you can
inject a stub for testing without AWS credentials:

```python
from guardrails_from_policy.policy_parser import PolicyParser
import json

class StubClient:
    def converse(self, **kwargs):
        return {"output": {"message": {"content": [{"text": json.dumps(my_fixture)}]}}}

parser = PolicyParser.__new__(PolicyParser)
parser.model_id = "stub"
parser.region = "us-east-1"
parser.max_policy_chars = 60_000
parser._client = StubClient()
policy = parser.parse("policy text")
```

---

## Idempotency

`deploy_guardrail` is idempotent:
- Lists existing guardrails by name.
- **New name** → `CreateGuardrail`.
- **Existing name** → `UpdateGuardrail` on the DRAFT, then `CreateGuardrailVersion`.

Tagging and KMS encryption only apply at creation time (Bedrock API limitation).

---

## IAM permissions required

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock-runtime:Converse",
    "bedrock:CreateGuardrail",
    "bedrock:UpdateGuardrail",
    "bedrock:ListGuardrails",
    "bedrock:GetGuardrail",
    "bedrock:CreateGuardrailVersion",
    "bedrock:TagResource"
  ],
  "Resource": "*"
}
```

If `--kms-key-id` is used, also add `kms:GenerateDataKey` and `kms:Decrypt` on that key.

---

## Teardown

The `deployer.py` module supports deletion via the same CLI:

```bash
# Delete by guardrail name (looks up the ID, then deletes)
policy2guardrail --delete --guardrail-name acme-aup-guardrail

# Or by guardrail ID
policy2guardrail --delete --guardrail-id <id>
```

To delete every guardrail this tool created in the current region:

```bash
REGION=${AWS_REGION:-us-east-1}
for ID in $(aws bedrock list-guardrails --region "$REGION" \
              --query 'guardrails[?starts_with(name, `acme-`) || starts_with(name, `policy2guardrail-`)].id' \
              --output text); do
  echo "Deleting guardrail $ID"
  aws bedrock delete-guardrail --guardrail-identifier "$ID" --region "$REGION"
done
```

There is no other persistent state — `policy2guardrail` writes nothing
to S3 or DynamoDB. Bedrock guardrails themselves have **no standing
hourly cost**; you are billed per `ApplyGuardrail` call. Leaving an
unused guardrail in place is free, so teardown is only necessary to
keep the console tidy.

If you used `--kms-key-id`, the CMK is yours — this tool never created
one, so it leaves your key untouched.

---

## Supported policy formats

| Extension | Dependency | Notes |
|---|---|---|
| `.txt`, `.md`, anything else | none | Read as UTF-8 |
| `.pdf` | `pypdf` (included by default) | Text-layer PDFs only; scanned PDFs need OCR pre-processing |
| `.docx` | `python-docx` (optional) | Install with `pip install 'guardrails-from-policy[docx]'` |

---

## Layout

```
guardrails-from-policy/
├── guardrails_from_policy/
│   ├── __init__.py          public API
│   ├── extractor.py         PDF / DOCX / TXT text extraction
│   ├── policy_parser.py     Claude-powered structured extraction
│   ├── guardrail_builder.py ParsedPolicy → boto3 request shape
│   ├── deployer.py          CreateGuardrail / UpdateGuardrail
│   └── cli.py               policy2guardrail entry point
├── examples/
│   ├── sample_policy.txt              realistic ACME AUP/data-classification policy
│   ├── sample_policy_expected_config.json  reference output for manual review
│   └── run_example.py                 end-to-end example script
├── tests/
│   ├── fixtures.py
│   ├── test_extractor.py
│   ├── test_policy_parser.py
│   ├── test_guardrail_builder.py
│   └── test_deployer.py
└── pyproject.toml
```
