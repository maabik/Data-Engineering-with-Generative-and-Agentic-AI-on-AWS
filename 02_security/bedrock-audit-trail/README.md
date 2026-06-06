# Bedrock Audit Trail

A deployable reference implementation that turns native Bedrock invocation logs
into a governance-grade audit trail. Covers the full pipeline: KMS-encrypted
destinations → Firehose transform → Iceberg dataset → Athena analytics →
QuickSight dashboards.

---

## Architecture

```
Bedrock InvokeModel/InvokeAgent
         │
         ▼ (requestMetadata: user_id, tenant_id, session_id, trace_id)
┌─────────────────────────┐
│  Bedrock Invocation     │   ← CDK provisions this config
│  Logging Configuration  │
│  Role: BedrockLogging   │
└────────┬────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
CloudWatch   S3 (raw)     ← both KMS-encrypted with CMK
Log Group    Bucket
    │
    ▼ (subscription filter → Firehose)
┌──────────────────────────────────────────────┐
│          Kinesis Firehose                     │
│                                              │
│  Transform Lambda:                           │
│  1. Parse invocation record                  │
│  2. Comprehend PII detection                 │
│  3. Redact + tokenize (reversible, KMS-S3)   │
│  4. Enrich: user_id / tenant_id / trace_id   │
│  5. Sampling policy (100% flagged, N% normal)│
└──────────────────┬───────────────────────────┘
                   │
                   ▼
          S3 Iceberg Bucket
          partitioned by:
            date / model_family / tenant_id
                   │
          ┌────────┴────────┐
          │                 │
          ▼                 ▼
    Glue Catalog      Athena Queries
    (bedrock_audit)   cost/users/PII/
                      latency/guardrails
                           │
                           ▼
                    QuickSight Dashboard
                    (4 sheets: Overview,
                     Security, Latency,
                     Users & Tenants)
```

---

## Quick start

```bash
cd 02_security/bedrock-audit-trail

# Run all tests (no AWS credentials needed)
pip install -e ".[dev]"
pytest

# Deploy CDK stack
pip install -e ".[dev]"
cd cdk
cdk deploy BedrockAuditTrail \
  --parameters AlarmEmail=ops@example.com \
  --parameters NormalTrafficSamplePercent=100

# After deploy, configure Bedrock logging using the stack outputs:
aws bedrock put-model-invocation-logging-configuration \
  --logging-config '{
    "cloudWatchConfig": {
      "logGroupName": "/aws/bedrock/invocation-logs",
      "roleArn": "<BedrockLoggingRoleArn output>",
      "largeDataDeliveryS3Config": {
        "bucketName": "<RawLogBucketName output>",
        "keyPrefix": "large-payloads/"
      }
    },
    "s3Config": {
      "bucketName": "<RawLogBucketName output>",
      "keyPrefix": "invocation-logs/"
    },
    "textDataDeliveryEnabled": true,
    "imageDataDeliveryEnabled": true,
    "embeddingDataDeliveryEnabled": true
  }'
```

---

## Components

### CDK Stack (`cdk/stacks/audit_trail_stack.py`)

Provisions everything in one `cdk deploy`:

| Resource | Purpose |
|---|---|
| `LogCmk` (KMS) | Encrypts S3 buckets, CW Log Group, Firehose, SNS |
| `PiiTokenCmk` (KMS) | Encrypts the reversible PII token store |
| `RawLogBucket` (S3) | Bedrock invocation log destination |
| `IcebergBucket` (S3) | Processed Iceberg dataset |
| `PiiTokenStoreBucket` (S3) | Reversible PII token mappings |
| `BedrockInvocationLogs` (CW) | Second log destination (1-year retention) |
| `BedrockLoggingRole` (IAM) | Allows Bedrock to write to CW + S3 |
| `TransformFunction` (Lambda) | Firehose record transformation |
| `DeliveryStream` (Firehose) | Buffers and delivers to Iceberg bucket |
| `GlueDatabase` + `InvocationTable` | Iceberg-typed Glue catalog |
| 5× CloudWatch Alarms | Cost spikes, PII rate, guardrail blocks, errors, injections |
| `AlarmTopic` (SNS) | Alarm notifications |
| S3 replication config | Optional cross-account aggregation |

**Stack parameters:**

| Parameter | Default | Description |
|---|---|---|
| `AlarmEmail` | `` | SNS email for alarms |
| `EnableCrossAccountReplication` | `false` | Enable S3 replication to central archive |
| `CentralArchiveBucketArn` | `` | Target ARN for replication |
| `NormalTrafficSamplePercent` | `100` | % of normal traffic to retain |

---

### Firehose Transform Lambda (`lambda/transform/`)

| File | Purpose |
|---|---|
| `parser.py` | Parses raw Bedrock log JSON into the audit schema; extracts text from all model body formats (Anthropic, Titan, Llama, Cohere, Mistral) |
| `pii.py` | Comprehend PII detection + reversible tokenization; stores `token_id → original` in the KMS-protected token store |
| `sampling.py` | Keeps 100% of PII/guardrail-flagged/error events; samples normal traffic at `SAMPLE_RATE`% |
| `transform.py` | Firehose handler: orchestrates parse → PII → sample → encode |

**Iceberg record schema** (key fields):

| Field | Type | Notes |
|---|---|---|
| `event_time` | timestamp | UTC |
| `request_id` | string | Bedrock requestId |
| `model_id` | string | Full model ID |
| `user_id`, `tenant_id`, `session_id`, `trace_id` | string | From `requestMetadata` |
| `input_tokens`, `output_tokens` | int | Token counts |
| `latency_ms` | int | End-to-end latency |
| `input_text_redacted`, `output_text_redacted` | string | PII-redacted text |
| `pii_detected`, `pii_entity_types`, `pii_token_ids` | bool/arrays | Comprehend findings |
| `guardrail_action` | string | `NONE` \| `INTERVENED` \| `BLOCKED` |
| `injection_detected` | bool | `PROMPT_ATTACK` filter triggered |
| `sampled` | bool | False = dropped by sampling policy |

---

### Trace Client (`trace_client/`)

Injects `user_id`, `tenant_id`, `session_id`, and `trace_id` into every Bedrock
call so records can be correlated across multi-step agent interactions.

```python
from trace_client import BedrockTracer

tracer = BedrockTracer(
    user_id="u123",
    tenant_id="acme",
    session_id="sess-abc",
)

# Direct Bedrock call — metadata injected automatically
response = tracer.converse(
    modelId="anthropic.claude-3-sonnet-20240229-v1:0",
    messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
)

# Child tracer — same trace_id, different user
child = tracer.child(user_id="u456")

# Context manager
from trace_client.tracer import trace_context
with trace_context(user_id="u1", tenant_id="acme") as t:
    t.converse(...)

# Strands agent
from trace_client import traced_agent
from strands import Agent
agent = Agent(model="anthropic.claude-3-sonnet-20240229-v1:0")
result = traced_agent(agent, "What is the capital of France?",
                      user_id="u1", tenant_id="acme")
```

The `trace_id` propagates through all child calls, enabling the
`v_trace_conversations` Athena view to stitch multi-turn agent sessions into a
single conversation record.

---

### Athena

**DDL** (`athena/ddl/`): Iceberg table definition (run once, or use the Glue
catalog table created by CDK).

**Views** (`athena/views/`):

| View | Purpose |
|---|---|
| `v_trace_conversations` | Aggregates per-trace_id: total tokens, P50/P95/P99 latency, PII/injection flags, error count |
| Cross-account view | Central-account view across all replicated source accounts |

**Sample queries** (`athena/queries/`):

| Query | Returns |
|---|---|
| `cost_per_tenant.sql` | Estimated USD cost by tenant/model/day |
| `top_users.sql` | Top 50 users by token consumption (7d) |
| `pii_detection_rate.sql` | PII rate % by model family and day |
| `latency_percentiles.sql` | P50/P95/P99 latency by model and day |
| `guardrail_trigger_rates.sql` | Block rate, intervention rate, top topics (30d) |
| `jailbreak_injection_detections.sql` | Recent blocked/injected events with redacted prompt preview |

---

### QuickSight Dashboard (`quicksight/`)

Four sheets:

| Sheet | Key visuals |
|---|---|
| **Overview** | KPIs (invocations, cost, PII rate, block rate), invocations over time, cost by tenant |
| **Security & Compliance** | PII trend, entity type breakdown, guardrail topics, injection detections, recent blocks table |
| **Latency & Performance** | P50/P95/P99 over time, latency by model, tokens-vs-latency scatter, error rate KPI |
| **Users & Tenants** | Top 25 users table, activity heatmap, stacked cost trend by tenant |

Deploy with:
```bash
python quicksight/deploy_dashboard.py \
  --account-id 123456789012 \
  --dataset-arn arn:aws:quicksight:us-east-1:123456789012:dataset/bedrock-invocations \
  --principal arn:aws:quicksight:us-east-1:123456789012:user/default/admin
```

---

### CloudWatch Alarms

| Alarm | Threshold | Condition |
|---|---|---|
| `bedrock-audit-cost-spike` | >1M input tokens in 5 min | Token usage anomaly |
| `bedrock-audit-pii-rate` | >50 PII events in 5 min | Data leak risk |
| `bedrock-audit-guardrail-block-rate` | >20 blocks in 5 min | Attack surge |
| `bedrock-audit-model-error-rate` | >10 errors in 5 min | Service degradation |
| `bedrock-audit-injection-detected` | >5 injections in 5 min | Active attack |

---

### Cross-account aggregation

Enable replication on deploy:

```bash
cdk deploy BedrockAuditTrail \
  --parameters EnableCrossAccountReplication=true \
  --parameters CentralArchiveBucketArn=arn:aws:s3:::my-central-log-archive
```

Then in the central account:
1. Grant `ReplicationRoleArn` the `s3:ReplicateObject` permission on the central bucket.
2. Create a Glue table (`bedrock_audit_central.invocations_all_accounts`) pointing at the central bucket.
3. Run the `cross_account.sql` view to query all accounts together.

---

## Teardown

```bash
cd cdk
cdk destroy BedrockAuditTrail
```

`cdk destroy` deletes the Firehose, Lambda, IAM roles, CloudWatch log
groups, and the Glue Iceberg database **stack metadata only**. Three
stateful resources remain by default and must be cleaned up explicitly:

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-east-1}

# 1. S3 / S3 Tables bucket holding the Iceberg dataset.
#    This is the standing cost (~$0.023/GB-month + per-request) — drop it
#    once you no longer need the audit history.
aws s3 rm "s3://bedrock-audit-trail-${ACCOUNT}-${REGION}/" --recursive
aws s3 rb "s3://bedrock-audit-trail-${ACCOUNT}-${REGION}"

# 2. Schedule deletion of the customer-managed KMS CMK used for the
#    Firehose buffer and the bucket. 7-day waiting period is the minimum.
KEY_ID=$(aws kms list-aliases --region "$REGION" \
           --query "Aliases[?AliasName=='alias/bedrock-audit-trail'].TargetKeyId | [0]" \
           --output text)
aws kms schedule-key-deletion --key-id "$KEY_ID" --pending-window-in-days 7

# 3. Glue catalog database (cascades to the registered Iceberg tables).
aws glue delete-database --name bedrock_audit_trail --region "$REGION"
```

If you ran the optional QuickSight dashboard step, also delete the
QuickSight dataset and analysis from the QuickSight console.

The Athena workgroup is shared with the rest of chapter 03, so it is
**not** deleted by this teardown.

### Cross-account variant

If you wired the central-bucket replication described above, tear down
the producer account first (steps above), then in the central account
empty and delete `bedrock-audit-central-${ACCOUNT}-${REGION}` and drop
the Glue database `bedrock_audit_central`.

---

## Testing

```bash
pytest                          # 56 tests, no AWS credentials needed
pytest tests/test_parser.py     # parse logic only
pytest tests/test_pii.py        # PII detection (mocked Comprehend)
pytest tests/test_transform.py  # Firehose handler (mocked end-to-end)
pytest tests/test_tracer.py     # BedrockTracer injection
pytest tests/test_sampling.py   # Sampling policy
```

---

## Chapter cross-references

| Feature | Chapter |
|---|---|
| Iceberg/S3 Tables dataset + Glue catalog | Chapter 3 (Data Lake) |
| Athena cost/usage analytics | Chapter 3 |
| QuickSight dashboard | Chapter 11 (BI & Visualization) |
| Bedrock Guardrails | Chapter 2 (Security) |
| KMS CMK encryption | Chapter 2 (`s3-kms-cmk` sample) |
