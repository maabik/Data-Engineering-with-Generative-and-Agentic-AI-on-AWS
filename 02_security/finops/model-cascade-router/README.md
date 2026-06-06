# model-cascade-router

## 1 — Overview

Cross-family Bedrock router: Nova Micro → Haiku → Sonnet → Opus, plus Llama/Mistral fallbacks. Two-stage classifier (zero-cost heuristic first, LLM fallback for low-confidence), config-driven floor/ceiling/task-label overrides, eval harness (LLM-as-judge + Bedrock Model Evaluation API). The cost lever: Claude 3 Opus = $0.075/1K output tokens; Nova Micro = $0.00014 — a 535× difference.

Most production workloads mix trivial factual lookups with genuine reasoning tasks; routing correctly saves 60–90% of model spend with no quality regression.

## 2 — Architecture

```
Prompt
  │
  ▼
TaskClassifier
  ├── Stage 1: Heuristic (zero cost, ~0ms)
  │     keyword signals + token length + conversation depth
  │     → TaskComplexity: trivial / simple / moderate / complex / expert
  │     → if confidence ≥ threshold → use result
  │
  └── Stage 2: LLM classifier (one Nova Micro call, ~$0.000005)
        meta-prompt sent to cheapest tier
        → structured JSON complexity + confidence
        (only triggered when heuristic confidence is low)
  │
  ▼
Tier Resolution
  ├── complexity → minimum tier (COMPLEXITY_TO_TIER map)
  ├── task_label overrides  (e.g. "code_generation" → "large")
  ├── config floor / ceiling
  └── start_tier = max(complexity_tier, override_tier, floor_tier)
  │
  ▼
Invoke (cheapest qualifying tier)
  │
  ├── success → RouteDecision
  └── refusal / throttle / error → escalate to next tier
                                   (up to max_retries)
```

## 3 — Prerequisites

- Python >= 3.11
- AWS account with Bedrock access in `us-west-2`
- CDK CLI and bootstrap (first deploy only): `npm install -g aws-cdk` then `cdk bootstrap aws://ACCOUNT_ID/us-west-2`
- `pip install aws-cdk-lib constructs`

## 4 — Installation

```bash
pip install -e .
pip install -e ".[dev]"
```

## 5 — Quick start

### Drop-in replacement for `bedrock-runtime.converse()`

```python
import boto3
from router import CascadeRouter

client = boto3.client("bedrock-runtime", region_name="us-east-1")
router = CascadeRouter(bedrock_client=client)

decision = router.route(
    messages=[{"role": "user", "content": [{"text": "What is the capital of France?"}]}],
)

print(decision.response["output"]["message"]["content"][0]["text"])
print(f"Tier: {decision.tier.tier_id}   Cost: ${decision.estimated_cost_usd:.6f}")
# Tier: nano   Cost: $0.000008
```

### Pin task types to a minimum tier

```python
from router import CascadeRouter, RouterConfig

cfg = RouterConfig(
    task_overrides={
        "code_generation": "large",    # never use nano/micro/haiku for code
        "legal_review":    "flagship", # always use Opus for legal
    },
    max_tier_id="large",               # never spend Opus money by default
)
router = CascadeRouter(config=cfg, bedrock_client=client)

decision = router.route(
    messages=[{"role": "user", "content": [{"text": "Write a merge sort in Python."}]}],
    task_label="code_generation",
)
```

### Dry-run: classify without invoking

```python
result = router.classify(
    messages=[{"role": "user", "content": [{"text": "What year did WWII end?"}]}]
)
print(result.complexity, result.confidence, result.reason)
# trivial  0.9  trivial keyword match
```

### Integration with agent-run-cost-tracer

```python
from router import CascadeRouter
from tracer import RunTracer

tracer = RunTracer(user_id="alice", tenant_id="acme", emit_backend="firehose")
router = CascadeRouter(bedrock_client=client)

decision = router.route(
    messages=messages,
    request_metadata={
        "trace_id": tracer.trace_id,
        "run_id": tracer.run_id,
        "user_id": "alice",
    },
)
tracer.record_model_span(
    decision.tier.model_id,
    decision.input_tokens,
    decision.output_tokens,
    decision.latency_ms,
)
```

## 6 — Default tier cascade

| Tier ID | Model | Input $/1K | Output $/1K | vs Opus |
|---|---|---|---|---|
| `nano` | amazon.nova-micro-v1:0 | $0.000035 | $0.00014 | 535x cheaper |
| `micro` | amazon.nova-lite-v1:0 | $0.00006 | $0.00024 | 313x cheaper |
| `small` | amazon.nova-pro-v1:0 | $0.0008 | $0.0032 | 23x cheaper |
| `medium` | claude-haiku-4-5 | $0.001 | $0.005 | 15x cheaper |
| `large` | claude-sonnet-4-6 | $0.003 | $0.015 | 5x cheaper |
| `flagship` | claude-opus-4-7 | $0.015 | $0.075 | baseline |

## 7 — Inference profile IDs

Important: on-demand throughput requires cross-region inference profile IDs. Use `us.amazon.nova-micro-v1:0`, not `amazon.nova-micro-v1:0`. The router's cost tracking strips `us./eu./ap./global.` prefixes automatically.

Available cross-region profiles (us-west-2):

- `us.amazon.nova-micro-v1:0`
- `us.amazon.nova-lite-v1:0`
- `us.amazon.nova-pro-v1:0`
- `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- `us.anthropic.claude-sonnet-4-6`
- `us.anthropic.claude-opus-4-7`

## 8 — Configuration reference

```python
RouterConfig(
    tiers=DEFAULT_TIERS,          # ordered cheapest-first
    min_tier_id=None,             # global floor (never go below)
    max_tier_id=None,             # global ceiling (never exceed)
    task_overrides={},            # {task_label: min_tier_id}
    quality_threshold=0.70,       # heuristic confidence needed before skipping LLM classifier
    retry_on_refusal=True,        # escalate on content_filtered / guardrail_intervened
    max_retries=3,                # max escalation steps
)
```

### Adding Llama or Mistral fallbacks

```python
from router.config import RouterConfig, DEFAULT_TIERS, LLAMA_TIERS

cfg = RouterConfig(
    tiers=DEFAULT_TIERS + LLAMA_TIERS,  # append as higher-index fallbacks
    max_tier_id="llama-large",
)
```

## 9 — Complexity to tier mapping

| Complexity | Default tier | Triggers |
|---|---|---|
| `trivial` | `nano` | "what is", "who is", true/false, short definitions |
| `simple` | `micro` | Short prompts < 80 tokens |
| `moderate` | `small` | Medium prompts, multi-turn depth < 6 |
| `complex` | `large` | "write", "implement", "analyse", long prompts, deep conversations |
| `expert` | `flagship` | "formal proof", "security audit", adversarial, research-grade |

## 10 — RouteDecision fields

| Field | Description |
|---|---|
| `tier` | `TierConfig` of the selected model |
| `model_id` | Effective model or inference profile ARN |
| `classifier` | `ClassifierResult` (complexity, confidence, reason, strategy) |
| `input_tokens` | Tokens consumed |
| `output_tokens` | Tokens generated |
| `estimated_cost_usd` | Cost for this invocation |
| `escalation_path` | List of tier IDs tried before landing here |
| `latency_ms` | Wall-clock time for the Bedrock call |
| `response` | Raw `bedrock-runtime.converse()` response |
| `error` | Set if all tiers exhausted |
| `success` | `True` if a valid response was obtained |

## 11 — Eval harness

Prove "model X is good enough for task Y" before flipping a routing rule.

### LLM-as-judge (local, fast)

```python
import boto3
from eval.harness import EvalHarness, EvalSuite

client = boto3.client("bedrock-runtime")
harness = EvalHarness(
    bedrock_client=client,
    judge_model_id="us.anthropic.claude-sonnet-4-6",
)

suite = EvalSuite.from_jsonl("eval/suites/qa_simple.jsonl")
report = harness.run_local(
    suite,
    model_id="amazon.nova-micro-v1:0",
    min_score=3.5,          # 1-5 scale; 3.5 = "mostly correct"
)
print(report.summary())
# Model: amazon.nova-micro-v1:0  Suite: qa_simple
# Passed 5/5 (100%)  avg score 4.60/5.0  total cost $0.0001
```

### Bedrock Model Evaluation (managed async, ~15 min)

The `run_managed()` method uploads the dataset JSONL to S3 automatically, then submits the job. Callers do not need to pre-upload.

```python
import boto3
from eval.harness import EvalHarness, EvalSuite

brt = boto3.client("bedrock-runtime", region_name="us-west-2")
harness = EvalHarness(bedrock_client=brt)

suite = EvalSuite.from_jsonl("eval/suites/qa_simple.jsonl")

job_arn = harness.run_managed(
    suite=suite,
    model_id="us.amazon.nova-micro-v1:0",
    output_s3_uri="s3://MY-EVAL-BUCKET/results/",
    role_arn="arn:aws:iam::ACCOUNT:role/EVAL-ROLE-NAME",
    eval_name="my-eval-job",          # optional
)

# Poll until complete (~15 min); timeout_s=1800 default
result = harness.poll_managed(job_arn, timeout_s=1800)
print(result["status"])   # "Completed"
```

`run_managed()` parameters:

| Parameter | Required | Description |
|---|---|---|
| `suite` | yes | `EvalSuite` instance |
| `model_id` | yes | Inference profile or model ID under test |
| `output_s3_uri` | yes | S3 URI prefix for job output |
| `role_arn` | yes | IAM role trusted by `bedrock.amazonaws.com` |
| `eval_name` | no | Job name; auto-generated if omitted |
| `dataset_s3_uri` | no | Override dataset S3 path; defaults to `output_s3_uri/datasets/<suite.name>.jsonl` |

### Writing eval suites

Suites are JSONL files, one case per line:

```jsonl
{"case_id": "qa-001", "task_label": "factual_qa", "messages": [{"role": "user", "content": [{"text": "What is the capital of France?"}]}], "expected_answer": "Paris"}
{"case_id": "code-001", "task_label": "code_generation", "messages": [{"role": "user", "content": [{"text": "Write a binary search in Python."}]}], "expected_answer": "A correct O(log n) implementation with -1 on miss."}
```

Run the eval, check `report.pass_rate`. If it meets your threshold, promote the routing rule.

## 12 — CDK deployment

Stack name: `ModelCascadeRouterEval`, region: `us-west-2`.

```bash
cd cdk
pip install aws-cdk-lib constructs
cdk bootstrap aws://ACCOUNT_ID/us-west-2   # first time only
cdk deploy ModelCascadeRouterEval
```

Resources provisioned:

| Resource | Details |
|---|---|
| KMS CMK | `alias/model-cascade-router-eval-key`, rotation enabled |
| S3 eval bucket | KMS-encrypted, 90-day expiry, auto-delete on stack destroy |
| IAM EvalRole | Trusted by `bedrock.amazonaws.com`; S3 read/write + `bedrock:InvokeModel` |
| IAM CallerRole | Submit/query eval jobs, upload datasets, `iam:PassRole` to EvalRole |

CloudFormation outputs:

| Output | Description |
|---|---|
| `EvalBucketName` | Pass as `output_s3_uri` base |
| `EvalRoleArn` | Pass as `role_arn` to `run_managed()` |
| `CallerRoleArn` | Assume this role from your harness process |
| `CmkArn` | KMS key ARN |

## 13 — Unit tests

```bash
pytest tests/ -v    # 82 tests, no AWS credentials needed
```

## 14 — Live integration tests

### Router live tests (Bedrock credentials only)

```bash
pytest tests/test_live.py -v
```

11 tests: heuristic classifier accuracy, `route()` returns real Bedrock response, cheap tier selected for trivial prompts, tokens and cost tracked, latency_ms positive, floor/ceiling enforcement, task_label override, `classify()` dry-run, `RouteDecision` has classifier result.

### Eval harness live tests

```bash
# Fast tests only (no managed job submission):
pytest tests/test_live_eval.py -v

# All tests including managed eval job (~15 min):
pytest tests/test_live_eval.py -v --run-slow
```

Fast tests (11): stack infra (S3 encrypted, KMS rotation, EvalRole trusted by bedrock, CallerRole exists), `run_local()` returns report, all 3 cases scored without error, cost is positive, pass_rate >= 33% on simple QA with `min_score=3.5`, `summary()` format correct, `to_dict()` JSON-serialisable.

Slow tests (4, requires `--run-slow`): `run_managed()` submits job and returns ARN, dataset JSONL uploaded to S3, `poll_managed()` waits for `Completed` status, result files written to S3 by Bedrock.

## 15 — Project structure

```
model-cascade-router/
├── router/
│   ├── __init__.py
│   ├── config.py              # TierConfig, RouterConfig, DEFAULT_TIERS
│   ├── classifier.py          # TaskClassifier, classify_heuristic, classify_llm
│   └── cascade.py             # CascadeRouter, RouteDecision
├── eval/
│   ├── __init__.py
│   ├── harness.py             # EvalHarness, EvalSuite, EvalReport
│   └── suites/
│       └── qa_simple.jsonl    # 5-case factual QA suite
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── router_eval_stack.py   # ModelCascadeRouterEvalStack
├── tests/
│   ├── conftest.py            # --run-slow flag
│   ├── test_classifier.py     # unit tests
│   ├── test_config.py         # unit tests
│   ├── test_cascade.py        # unit tests
│   ├── test_eval_harness.py   # unit tests
│   ├── test_live.py           # 11 live tests (routing + Bedrock)
│   └── test_live_eval.py      # 15 live tests (EvalHarness)
└── pyproject.toml
```
