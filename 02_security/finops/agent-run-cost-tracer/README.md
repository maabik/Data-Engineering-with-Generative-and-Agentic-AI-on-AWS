# agent-run-cost-tracer

## 1 — Overview

A correlation library that surfaces the hidden cost structure of agentic AI. Every Bedrock/Strands invocation gets a `trace_id` injected into `requestMetadata`. A nightly stitcher assembles all sub-calls for one user turn into a RunRecord showing total tokens, total dollars, and a `cost_amplification` multiplier. Without it, a $0.002 orchestration call can hide a $0.04 turn (20× amplification).

A single user question that looks cheap at the LLM level ($0.002) may have triggered:

- 12 model invocations (orchestration + sub-agents)
- 4 KB retrievals with query re-embedding
- 2 embedding refreshes
- 6 tool calls (web search, code interpreter, S3)

Actual cost: $0.04 — a **20× amplification** invisible to standard Bedrock billing reports.

## 2 — Architecture

```
User turn
    │
    ▼
RunTracer (context manager / Strands integration)
    │  injects trace_id into requestMetadata
    │  records RunSpan for every sub-call
    │
    ├── model invocations  → Bedrock converse() / invoke_model()
    ├── tool calls         → Strands callback_handler events
    ├── KB retrievals      → bedrock-agent-runtime.retrieve()
    └── embeddings         → bedrock-runtime.invoke_model()
    │
    ▼
Emit backend
    ├── Firehose → S3 (production)
    ├── CloudWatch Logs
    ├── stdout (local dev)
    └── none (unit tests)
    │
    ▼
Nightly stitcher (Lambda / local CLI)
    │  groups spans by run_id
    │  computes cost_amplification
    │
    ▼
ai_finops.agent_runs (Iceberg / Athena)
    │
    ▼
FinOps dashboards (QuickSight / Grafana)
```

## 3 — Prerequisites

- Python >= 3.11
- AWS account with Bedrock access in `us-west-2`
- CDK CLI and bootstrap (first deploy only):
  ```bash
  npm install -g aws-cdk
  cdk bootstrap aws://ACCOUNT_ID/us-west-2
  ```
- `pip install aws-cdk-lib constructs` (CDK Python)

## 4 — Installation

```bash
pip install -e .            # core library
pip install -e ".[dev]"     # adds pytest, pytest-mock, freezegun
```

## 5 — Quick Start

### Context manager

```python
from tracer import run_context

with run_context(
    user_id="alice",
    tenant_id="acme",
    session_id="sess-abc",
    workload="revenue-qa",
    emit_backend="firehose",
) as t:
    response = t.converse(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        messages=[{"role": "user", "content": [{"text": "What was Q3 revenue?"}]}],
    )
    results = t.retrieve("kb-finance-001", "Q3 revenue breakdown")
```

### Strands agent

```python
from strands import Agent
from tracer import traced_strands_agent

agent = Agent(model="anthropic.claude-3-sonnet-20240229-v1:0", tools=[...])
result, tracer = traced_strands_agent(
    agent,
    "Summarise last quarter's revenue and compare to budget",
    user_id="alice",
    tenant_id="acme",
    workload="finance-chat",
)
summary = tracer.run_summary()
print(f"Cost amplification: {summary['cost_amplification']}×")
```

### Manual span recording

```python
from tracer import RunTracer, SpanType

t = RunTracer(user_id="u1", tenant_id="acme", emit_backend="none")
s = t.record_model_span(
    "anthropic.claude-3-sonnet-20240229-v1:0",
    input_tokens=1200, output_tokens=350, duration_ms=1800,
)
t.flush()
```

## 6 — RunSpan Fields

| Field | Type | Description |
|---|---|---|
| `span_id` | str | UUID per sub-call |
| `trace_id` | str | Shared across all spans in a user turn |
| `run_id` | str | UUID for this tracer instance |
| `span_type` | str | `model`, `tool`, `retrieval`, `embedding`, `agent`, `sub_agent` |
| `model_id` | str | Bedrock model ID (model spans only) |
| `input_tokens` | int | Prompt token count |
| `output_tokens` | int | Completion token count |
| `estimated_cost_usd` | float | Cost estimate based on built-in pricing table |
| `tool_name` | str | Tool name (tool spans) |
| `tool_success` | bool | Whether the tool call succeeded |
| `kb_id` | str | Knowledge base ID (retrieval spans) |
| `retrieved_chunks` | int | Number of chunks returned |
| `embedding_model` | str | Embedding model ID |
| `embedding_input_tokens` | int | Tokens submitted for embedding |
| `user_id` | str | Attribution |
| `tenant_id` | str | Attribution |
| `workload` | str | Logical workload name |
| `error` | str | Error message if span failed |

## 7 — RunRecord (stitcher output)

| Field | Type | Description |
|---|---|---|
| `run_id` | str | Groups all spans for one turn |
| `total_estimated_cost_usd` | float | Full turn cost |
| `cost_amplification` | float | `total_cost / cheapest_single_model_call` |
| `model_invocations` | int | Total LLM calls |
| `tool_invocations` | int | Total tool calls |
| `retrieval_calls` | int | KB queries |
| `embedding_calls` | int | Embedding API calls |
| `orchestration_cost_usd` | float | First model call (orchestrator) |
| `retrieval_cost_usd` | float | Embedding call costs |
| `model_breakdown` | dict | Per-model `{invocations, tokens, cost_usd}` |
| `wall_clock_ms` | int | End-to-end latency |

## 8 — Pricing Table

Built-in pricing for 15 model families ($/1K tokens, input / output):

| Family prefix | Input | Output |
|---|---|---|
| `anthropic.claude-3-5-sonnet` | $0.003 | $0.015 |
| `anthropic.claude-3-5-haiku` | $0.0008 | $0.004 |
| `anthropic.claude-3-sonnet` | $0.003 | $0.015 |
| `anthropic.claude-3-haiku` | $0.00025 | $0.00125 |
| `anthropic.claude-3-opus` | $0.015 | $0.075 |
| `amazon.nova-pro` | $0.0008 | $0.0032 |
| `amazon.nova-lite` | $0.00006 | $0.00024 |
| `amazon.nova-micro` | $0.000035 | $0.00014 |
| `amazon.titan-embed` | $0.00002 | — |
| `cohere.command-r-plus` | $0.003 | $0.015 |

Override at runtime: `RunTracer(pricing_overrides={"my.model": (0.001, 0.005)})`.

> Cross-region inference profile IDs (e.g. `us.amazon.nova-micro-v1:0`) are supported — the `us.`/`eu.`/`ap.`/`global.` prefix is stripped automatically before the pricing lookup.

## 9 — Emit Backends

| Backend | When to use |
|---|---|
| `firehose` | Production — Kinesis Firehose → S3 |
| `cloudwatch` | Lambda functions with CW log groups |
| `stdout` | Local development |
| `none` | Unit tests |

Auto-selection priority:

1. Explicit `emit_backend=` constructor argument
2. `FINOPS_FIREHOSE_STREAM` env var → selects `firehose`
3. `FINOPS_CW_LOG_GROUP` env var → selects `cloudwatch`
4. `FINOPS_EMIT_BACKEND` env var → explicit override
5. Falls back to `stdout`

The CloudWatch backend automatically creates the log stream (`agent-run-cost/<run_id[:8]>`) on first emit.

## 10 — CDK Deployment

Stack name: `AgentRunCostTracer`, region: `us-west-2`.

```bash
cd cdk
pip install aws-cdk-lib constructs
cdk deploy AgentRunCostTracer
```

Resources provisioned:

| Resource | Details |
|---|---|
| KMS CMK | `alias/agent-run-cost-tracer-key`, rotation enabled, RETAIN on delete |
| S3 spans bucket | KMS-encrypted, 90-day IA transition, 1-year expiry, SSL enforced |
| Kinesis Firehose | Stream `agent-run-cost-spans` → S3, 60s/5 MB buffer, prefix `spans/yyyy/MM/dd/` |
| CloudWatch log group | `/agent-run-cost-tracer/spans`, 3-month retention, CMK-encrypted |
| IAM TracerRole | `firehose:PutRecord`, `logs:PutLogEvents` on the above resources |

CloudFormation outputs (used to configure `RunTracer`):

| Output | Value |
|---|---|
| `FirehoseStreamName` | `agent-run-cost-spans` |
| `SpansBucketName` | auto-generated bucket name |
| `CwLogGroupName` | `/agent-run-cost-tracer/spans` |
| `TracerRoleArn` | IAM role ARN |
| `CmkArn` | KMS key ARN |

## 11 — Nightly Stitcher

The stitcher runs as a nightly Lambda (or CLI) to assemble RunRecords:

```python
from datetime import date
from stitcher.loader import nightly_stitch

count = nightly_stitch(
    span_bucket="my-finops-spans",
    span_prefix="agent-spans/",
    target_date=date.today(),
    runs_bucket="my-finops-runs",
    runs_prefix="agent-runs/",
)
print(f"Stitched {count} runs")
```

Output: `s3://my-finops-runs/agent-runs/date=2024-01-15/runs.ndjson`

## 12 — Athena Views

| View | Purpose |
|---|---|
| `ai_finops.v_run_cost_breakdown` | Primary FinOps view — cost by run with amplification |
| `ai_finops.v_expensive_runs` | Top 500 most expensive runs in the last 7 days |
| `ai_finops.v_span_waterfall` | Timeline of sub-calls within a run with cumulative cost |

The `amplification_tier` column in `v_expensive_runs` flags runs as:

- `critical` — >= 20x amplification
- `high` — >= 10x
- `medium` — >= 5x
- `low` — < 5x

## 13 — Unit Tests

```bash
pytest tests/ -v    # 70 tests, no AWS credentials needed
```

## 14 — Live Integration Tests

Two live test files. Both require AWS credentials and the deployed `AgentRunCostTracer` stack.

**Bedrock-only tests** (no stack needed beyond credentials):

```bash
pytest tests/test_live.py -v
```

8 tests: RunTracer records model span with correct fields, requestMetadata injected without error, cost is positive, two-call accumulation (`invocations==2`), `run_context()` yields tracer and flushes, trace_id is stable, `run_summary()` contains required keys, tool span recorded.

**Emit infrastructure tests** (requires deployed `AgentRunCostTracer` stack):

```bash
pytest tests/test_live_emit.py -v
```

13 tests:

- Stack infra: Firehose stream ACTIVE, CW log group exists, S3 bucket encrypted, KMS CMK enabled with rotation
- Firehose path: single span PUT succeeds, `FINOPS_FIREHOSE_STREAM` env var auto-selects backend, 3-call batch all PUTs succeed, Firehose destination is correct S3 bucket
- CloudWatch path: log stream auto-created on first emit, span JSON content verified (span_type, tenant_id, input_tokens), `FINOPS_CW_LOG_GROUP` env var auto-selects backend
- `run_context()` flushes `RUN_SUMMARY` event to Firehose on context exit

## 15 — Project Structure

```
agent-run-cost-tracer/
├── tracer/
│   ├── __init__.py
│   └── run_tracer.py        # RunTracer, RunSpan, SpanType, run_context
├── stitcher/
│   ├── __init__.py
│   ├── stitch.py            # stitch_runs(), RunRecord
│   └── loader.py            # load_spans_from_s3(), nightly_stitch()
├── athena/
│   ├── ddl/
│   │   ├── create_spans_table.sql
│   │   └── create_runs_table.sql
│   └── views/
│       ├── v_run_cost_breakdown.sql
│       ├── v_expensive_runs.sql
│       └── v_span_waterfall.sql
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── tracer_stack.py  # AgentRunCostTracerStack
├── tests/
│   ├── test_run_tracer.py   # 27 unit tests
│   ├── test_stitch.py       # 31 unit tests
│   ├── test_loader.py       # 12 unit tests
│   ├── test_live.py         # 8 live tests (Bedrock calls)
│   └── test_live_emit.py    # 13 live tests (Firehose + CloudWatch)
└── pyproject.toml
```
