# Online Agent Evaluation

Production monitoring for every Bedrock agent in the account.

This project **discovers** all active agents — AgentCore Runtimes (across the
two regions used in this repo) **and** classic Bedrock Agents — and attaches a
**sampled LLM-as-judge evaluator** to each one. A small percentage of real
production traffic is scored on correctness, helpfulness, and safety, and the
verdicts are emitted as CloudWatch metrics, full traces in CloudWatch Logs,
and JSONL records in S3 for ad-hoc Athena queries.

There is no application code change required: agents do not call the
evaluator. The evaluator subscribes to each agent's invocation log group and
processes a sampled stream out-of-band, so production latency is not affected.

## Architecture

```
   Production traffic
         │
         ▼
 ┌───────────────────────────────┐
 │ AgentCore Runtime / Bedrock   │
 │ Agent  (logs to CloudWatch)   │
 └───────┬───────────────────────┘
         │  every invocation
         ▼
 ┌───────────────────────────────┐
 │ CloudWatch log group          │
 │ /aws/bedrock-agentcore/...    │
 │ /aws/bedrock/agents/...       │
 └───────┬───────────────────────┘
         │  subscription filter (5% sample, regex on entrypoint events)
         ▼
 ┌───────────────────────────────┐    Bedrock      ┌──────────────────────┐
 │ Lambda  oae-evaluator         │───InvokeModel──►│ Claude Sonnet 4.6    │
 │  - parses request/response    │                 │ (LLM-as-judge)       │
 │  - invokes judge model        │◄────verdict─────│                      │
 │  - emits CW metrics           │                 └──────────────────────┘
 │  - writes JSONL to S3         │
 └───────┬───────────────────────┘
         │
         ├──► CloudWatch namespace  OnlineAgentEvaluation
         │      metrics: Score, Correctness, Helpfulness, Safety
         │      dimensions: AgentName, AgentType, Region
         │
         └──► s3://oae-${ACCOUNT}-${REGION}/verdicts/
                  agent_name=…/dt=YYYY-MM-DD/*.jsonl
```

The evaluator Lambda is deployed **once per region** (`us-east-1` and
`us-west-2` in this repo). The bootstrap script subscribes it to every
discovered agent's log group with an inclusion regex pattern so only
entrypoint-level events (request + response pairs) trigger evaluation; chatty
internal traces are ignored.

## What gets evaluated

For each sampled invocation the judge returns a JSON object:

```json
{
  "scores": {
    "correctness":  {"score": 0-1, "reason": "..."},
    "helpfulness":  {"score": 0-1, "reason": "..."},
    "safety":       {"score": 0-1, "reason": "..."}
  },
  "overall":  0-1,
  "verdict":  "pass | warn | fail",
  "summary":  "1-2 sentence rationale"
}
```

The judge prompt is **agent-aware**: it uses each agent's name and (if
available) description to set context-appropriate expectations. For example,
`sql_evaluations_agent` (the SQL Evaluations Agent) is judged on whether its findings are
specific and actionable; `sales_reporting_agent` is judged on whether the
answer cites concrete numbers from the schema; the `data-engineering-orchestrator-agent`
is judged on whether the routing decision was reasonable.

## Sampling

Default sample rate is **5%** (`SAMPLE_RATE=0.05`). Sampling happens inside
the Lambda — every invocation log event still triggers Lambda, but only the
sampled fraction goes through to the judge. This keeps the judge spend
proportional to traffic volume but caps it at the sample rate.

You can change the rate per agent by setting an environment variable on the
Lambda or by calling `python3 scripts/set_sample_rate.py <agent-name> <rate>`
which writes a per-agent override to a DynamoDB rate table.

## Sinks

| Sink                     | What it stores                                           |
|--------------------------|----------------------------------------------------------|
| CloudWatch Metrics       | Per-dimension scores (`AgentName`, `AgentType`, `Region`) for alarming on score drift |
| CloudWatch Logs          | Structured per-evaluation log lines (full request, response, verdict) |
| S3 (`oae-…/verdicts/…`)  | JSONL partitioned by `agent_name=` and `dt=`, queryable via Athena |
| **Bedrock Evaluations console** (batch layer) | One managed `bedrock create-evaluation-job` per agent per day, fed by the captured BYOI dataset under `s3://oae-…/dataset/`. Visible at Console → Bedrock → Evaluations & observability → Evaluations. |

### Batch layer (Bedrock Evaluations service)

Alongside the online layer, a second Lambda (`oae-batch-eval`) runs daily on
an EventBridge schedule (`cron(15 6 * * ? *)`, 06:15 UTC). For each agent in
the registry it:

1. Concatenates yesterday's BYOI rows (`prompt` + `modelResponses[]`) from
   `s3://${VERDICTS_BUCKET}/dataset/agent_name=…/dt=…/` into a single JSONL.
2. Calls `bedrock create-evaluation-job` with `applicationType=ModelEvaluation`,
   `inferenceConfig.models[].precomputedInferenceSource` (BYOI — re-use the
   captured production responses, no re-inference), and built-in metrics
   `Builtin.Correctness`, `Builtin.Helpfulness`, `Builtin.Coherence`.
3. The judge model is the inference profile
   `us.anthropic.claude-haiku-4-5-20251001-v1:0` (override with
   `BATCH_JUDGE_PROFILE`). Note: Claude Sonnet 4.6 is **not** accepted by the
   Bedrock Evaluations service today — use a Haiku/Nova profile.

To trigger an ad-hoc run (e.g. right after deploy):

```bash
python3 scripts/run_batch_now.py            # yesterday (UTC)
python3 scripts/run_batch_now.py 2026-06-03 # specific date
```

Each submission appears on the Bedrock console as `oae-<agent>-<date>-<epoch>`.

A pre-built CloudWatch dashboard JSON is included in `dashboards/` — apply it
with `aws cloudwatch put-dashboard --cli-input-json file://dashboards/oae-overview.json`
after deploy.

## Deploy

```bash
bash deploy.sh
```

Single-shot, idempotent. What it does:

1. Provisions an S3 verdicts bucket, IAM role, and the evaluator Lambda in
   each target region (`us-east-1`, `us-west-2`).
2. Provisions a DynamoDB table (`oae-rates`) that holds per-agent sample rate
   overrides — single-region, replicated reads via `boto3` from each Lambda.
3. Discovers every AgentCore Runtime (both regions) and classic Bedrock Agent.
4. Resolves each agent's CloudWatch log group, ensuring it exists and has a
   reasonable retention policy.
5. Creates a subscription filter on each log group pointing at the regional
   evaluator Lambda, with an inclusion regex pattern that matches only
   entrypoint events.
6. Writes a registry to `s3://oae-${ACCOUNT}-${REGION}/registry.json` so the
   Lambda can resolve agent metadata at runtime.

Re-run `deploy.sh` after deploying any new agent to attach evaluation to it.

## Listing what's evaluated

```bash
python3 scripts/list_evaluated_agents.py
```

Prints a table of every agent the evaluator is currently subscribed to, with
its sample rate and the number of verdicts written in the last 24 hours.

## Tearing it down

```bash
bash teardown.sh
```

Removes subscription filters from each agent's log group first (so further
invocations are not evaluated), then deletes the Lambda, role, DynamoDB
table, and (with `--purge-bucket`) the S3 verdicts bucket. Agent runtimes
are **never** touched.

## Project layout

```
online-agent-evaluation/
├── evaluator/
│   ├── lambda_function.py    # subscription handler: sample → judge → emit
│   ├── judge_prompt.py       # agent-aware judge prompt builder
│   ├── requirements.txt      # boto3 (preinstalled in Lambda) + nothing else
│   └── Dockerfile            # only used if you want a container deploy
├── scripts/
│   ├── discover_agents.py    # enumerates AgentCore + Bedrock agents
│   ├── list_evaluated_agents.py
│   ├── set_sample_rate.py
│   └── replay_to_judge.py    # re-evaluate a past invocation by request id
├── dashboards/
│   └── oae-overview.json     # CloudWatch dashboard
├── deploy.sh                 # one-shot deploy
└── teardown.sh               # one-shot teardown
```
