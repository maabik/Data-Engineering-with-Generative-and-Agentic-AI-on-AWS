# data-contract-agent

Strands agent on **Bedrock AgentCore Runtime** that turns the
[`data-profiling-agent`](../data-profiling-agent/) insights JSON into a
**versioned data contract** (dbt-flavoured YAML) and, on every
subsequent run, enforces that contract against the live Glue Catalog
table — flagging breaking changes *before* the data-architect-agent
locks them into the catalog.

The seven MCP tools (`read_insights`, `read_contract`, `write_contract`,
`describe_glue_table`, `diff_contract_vs_table`,
`compile_contract_to_deequ_checks`, `write_verdict`) live behind one
Lambda exposed via **AgentCore Gateway**.

```
   profiling agent writes                          Cognito JWT
   s3://.../insights/<file>.json                         │
            │                                            ▼
            ▼                                     AgentCore Runtime  (data_contract_agent)
   EventBridge ──► data-contract-agent-bridge ──► Strands agent container
                                                         │
                                                MCP-over-HTTP, JWT
                                                         ▼
                                          AgentCore Gateway (data-contract-agent-gw)
                                                         │
                                          Lambda invoke (GATEWAY_IAM_ROLE)
                                                         ▼
                          ┌──────────────────────────────────────────────────┐
                          │  data-contract-agent-tools (Lambda)              │
                          │  • read_insights                                 │
                          │  • read_contract                                 │
                          │  • write_contract                                │
                          │  • describe_glue_table                           │
                          │  • diff_contract_vs_table                        │
                          │  • compile_contract_to_deequ_checks              │
                          │  • write_verdict                                 │
                          └────────────────────┬─────────────────────────────┘
                                               │ S3 read/write + Glue read
                                               ▼
                          AUTHOR mode (no contract yet)
                            s3://${BUCKET}/contracts/<table>.yaml
                            s3://${BUCKET}/contract-deequ-checks/<table>.json
                            s3://${BUCKET}/contract-verdicts/<file>.json
                                               │
                          ENFORCE mode (contract exists)
                            diff contract <-> live Glue table
                            s3://${BUCKET}/contract-verdicts/<file>.json
                            s3://${BUCKET}/contract-deequ-checks/<table>.json   (refreshed)
```

The bridge fires on the **same** `insights/` prefix the architect agent
listens to, so the contract verdict lands in the same window the
architect would otherwise lock the schema in. The verdict is **advisory**
— the chapter-12 orchestrator (or a human) decides whether to act on a
breaking finding.

## Why a contract agent (vs. just running Deequ?)

Deequ is a *value-level* checker that runs row-by-row against a fixed
schema. A contract is the *spec* the schema is supposed to match. The
two are complementary — they answer different questions, and the
agent's job is to enforce the parts only it can see and to compile the
rest into Deequ checks the data-quality-deequ-agent can run.

### What Deequ already covers — value/row checks

The contract agent **does not reimplement these**. It compiles them
into a Deequ checks JSON the data-quality-deequ-agent picks up.

| Contract clause | Deequ constraint |
| --- | --- |
| `required: true` (not-null) | `isComplete(col)` |
| `unique: true` / primary key | `isUnique(col)`, `hasUniqueness(cols, 1.0)` |
| `allowed_values: [A, B, C]` (enum) | `isContainedIn(col, allowed)` |
| `pattern: '^[A-Z]{3}$'` (regex) | `hasPattern(col, regex)` |
| `min: 0`, `max: 100` (numeric range) | `satisfies(col >= min)`, `satisfies(col <= max)` |
| `min_row_count: N` | `hasSize(_ >= N)` |
| `nullable_threshold: 0.05` | `hasCompleteness(col, 0.95)` |
| Statistical bounds (mean, stddev) | `hasMean`, `hasStandardDeviation` |
| Foreign-key referential integrity | `hasReferentialIntegrity(...)` |

### What Deequ can't express — schema / metadata / temporal

These are the contract agent's **exclusive territory**. Deequ runs
against a fixed schema, so it can't compare versions or reason about
catalog metadata.

| Contract clause | Why Deequ can't | Who checks it |
| --- | --- | --- |
| Column **added** | Deequ runs against a fixed schema; can't compare versions | `diff_contract_vs_table` |
| Column **removed** | Same — Deequ would just blow up on a missing column, not classify it | `diff_contract_vs_table` |
| Column **type changed** (string→int) | Deequ asserts a type but doesn't *detect change* | `diff_contract_vs_table` |
| Column **renamed** | Pure schema diff, not a value check | `diff_contract_vs_table` |
| `description:` populated | Glue table metadata, not row data | `diff_contract_vs_table` |
| `owner:` / `glossary_terms:` attached | Glue catalog metadata / DataZone | (future) |
| `pii: true` tag present | Catalog metadata | (future) |
| `freshness: max_age_hours: 24` | Needs `max(updated_at)` + wall clock | (future) |
| Contract **version** / `deprecated_columns:` | Cross-run history concept | `diff_contract_vs_table` |

The split is the design: the contract agent owns *spec ↔ live-catalog
diffs and metadata*, the data-quality-deequ-agent owns *spec-driven row
checks*, and the contract agent feeds the latter via
`compile_contract_to_deequ_checks`.

## Two modes

The agent auto-detects which mode it's in based on whether
`s3://${BUCKET}/contracts/<table>.yaml` already exists.

### AUTHOR mode (no contract yet for this dataset)

1. `read_insights(insights_uri)` — pull in the profiling agent's JSON.
2. `read_contract(table)` → `exists: false`.
3. Draft a dbt-flavoured contract from the insights:
   - One `columns[]` entry per profiled column with snake-cased
     `name`, normalised Glue/Hive `type`, and `required: true` when
     the profile shows < 1% null.
   - `description` from `insights.summary`.
   - `deprecated_columns: []` (empty until something is retired).
4. `write_contract(table, contract)` → persists YAML to S3.
5. `compile_contract_to_deequ_checks(table, contract)` → writes the
   Deequ checks JSON.
6. `write_verdict(insights_uri, {status: "ok", mode: "author", …})`.

### ENFORCE mode (contract already exists)

1. `read_insights(insights_uri)`.
2. `read_contract(table)` → `exists: true, contract: {...}`.
3. `describe_glue_table(database, table)` — live Glue schema +
   metadata. If the table doesn't exist yet, the diff still runs and
   surfaces a `table_missing` finding (expected on a brand-new dataset
   the architect hasn't curated yet).
4. `diff_contract_vs_table(contract, glue_table)` — pure function,
   classifies findings:

| Severity | Triggers |
| --- | --- |
| `breaking` | required column missing from live table; column type changed; table doesn't exist when contract expects it |
| `warning` | optional column missing; deprecated column still present; description missing on the live table |
| `info` | live table has a column the contract has not declared (additive, safe) |

5. `compile_contract_to_deequ_checks(table)` — refresh the Deequ checks
   JSON in case the contract was edited.
6. `write_verdict(insights_uri, {status, mode: "enforce", findings, …})`.

## Contract format

```yaml
table: violations_curated
description: NYC parking-violation summons, one row per ticket.

columns:
  - name: id
    type: bigint
    required: true
    unique: true
  - name: fine
    type: double
    required: true
    min: 0
    max: 10000
  - name: status
    type: string
    required: true
    allowed_values: [open, paid, dismissed]
  - name: respondents
    type: string
    nullable_threshold: 0.05    # up to 5% nulls tolerated
  - name: violation_date
    type: date
    required: true

min_row_count: 1
deprecated_columns: []          # columns the contract has retired
```

Type names are normalised: `varchar(255)` ≈ `string`, `bigint` ≈ `long`,
`bool` ≈ `boolean`, etc., so a contract doesn't have to mirror Glue's
exact spelling.

## Verdict format

The agent's final output (and the object written to
`s3://${BUCKET}/contract-verdicts/<file>.json`) is a single JSON object:

```json
{
  "status": "breaking",
  "mode": "enforce",
  "table": "violations_curated",
  "contractUri": "s3://.../contracts/violations_curated.yaml",
  "checksUri":   "s3://.../contract-deequ-checks/violations_curated.json",
  "totalFindings": 2,
  "breaking": 1, "warning": 1, "info": 0,
  "findings": [
    {"severity": "breaking", "code": "type_changed", "column": "fine",
     "contractType": "double", "liveType": "string",
     "message": "column 'fine' type changed: contract=double live=string"},
    {"severity": "warning",  "code": "description_missing",
     "message": "live Glue table has no Description set"}
  ],
  "summary": "Producer changed `fine` from double to string. Block downstream until the upstream feed is restored or the contract is updated."
}
```

## Tools

| Tool                                | Input                                    | Output                                                                                       |
| ----------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------- |
| `read_insights`                     | `insights_uri`                           | `{ok, insightsUri, insights:{...}}`                                                          |
| `read_contract`                     | `table`                                  | `{ok, exists, table, contractUri, contract?, yaml?}`                                         |
| `write_contract`                    | `table, contract`                        | `{ok, table, contractUri, bytes}`                                                            |
| `describe_glue_table`               | `database, table`                        | `{ok, exists, database, table, description, location, format, columns[], partitionKeys[], parameters}` |
| `diff_contract_vs_table`            | `contract, glue_table`                   | `{ok, status, totalFindings, breaking, warning, info, findings[]}`                           |
| `compile_contract_to_deequ_checks`  | `table, contract?`                       | `{ok, table, checksUri, checkCount}`                                                         |
| `write_verdict`                     | `insights_uri, verdict`                  | `{ok, verdictUri, bytes}`                                                                    |

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with buildx)
or `finch` for `linux/arm64` builds (AgentCore Runtime requires arm64).
**Deploy `data-profiling-agent` first** — this project piggybacks on its
S3 bucket (default `data-profiling-agent-${ACCOUNT}-${REGION}`) so the EventBridge rule
has something to listen to. The architect agent does not need to be
deployed for AUTHOR mode; ENFORCE mode reads from
`data_architect_agent_curated.<table>` (override via `GLUE_DATABASE=...`) once it's there.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`,
`PREFIX=data-contract-agent`, `DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`, `GLUE_DATABASE=data_architect_agent_curated`.
The script is idempotent — re-runs update everything in place.

The deploy creates: four IAM roles (lambda, bridge, gateway, runtime),
a Cognito user pool / resource server / app client / test user, a
Secrets Manager secret, a **PyYAML Lambda layer**
(`data-contract-agent-pyyaml`, built from `PyYAML==6.0.2` with a
manylinux2014 wheel for python3.12), the tools Lambda (with the layer
attached), an AgentCore Gateway with a Lambda target and inline tool
schema, an ECR repo + the agent container image, the AgentCore Runtime,
the bridge Lambda, and an EventBridge rule that fires on
`s3:ObjectCreated` under `insights/`. No Glue database or job is
created — the agent is read-only against the catalog.

## Live test

The chain is `landing/file.csv` → profiling-agent insights →
**contract-agent verdict (parallel to architect-agent curation)**:

```
# (assumes data-profiling-agent already deployed)
aws s3 cp ../data-profiling-agent/sample/violations.csv \
    s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv

# wait ~3-5 minutes (DataBrew profile + insights write + agent run), then:
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/contracts/violations_curated.yaml -
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/contract-verdicts/violations.json - | jq .
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/contract-deequ-checks/violations_curated.json - | jq .
```

You can also bypass EventBridge and invoke the runtime directly with a
known insights URI:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=data-contract-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://data-profiling-agent-${ACCOUNT}-${REGION}/insights/violations.json
```

## Layout

```
data-contract-agent/
├── deploy.sh                    # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py     # MCP tools target (the seven tools)
├── agent/
│   ├── data_contract_agent.py   # Strands @tool wrappers + entrypoint
│   ├── Dockerfile               # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py    # insights/ -> Cognito token -> Runtime
├── scripts/invoke_agent.py      # manual invocation helper
└── README.md
```

## Teardown

```
./teardown.sh
```

Deletes everything this project created — EventBridge rule, both
Lambdas, every published version of the PyYAML layer, the AgentCore
Runtime, the Gateway and target, the ECR repo, the Cognito pool, the
Secrets Manager secret, and all four IAM roles. The shared S3 bucket
is left intact (it belongs to data-profiling-agent); set
`DELETE_DCA_DATA=1` to also empty the `contracts/`,
`contract-verdicts/`, and `contract-deequ-checks/` prefixes.
