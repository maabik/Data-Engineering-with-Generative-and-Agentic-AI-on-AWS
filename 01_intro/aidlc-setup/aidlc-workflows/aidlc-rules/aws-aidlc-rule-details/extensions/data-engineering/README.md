# AI-DLC Data Engineering Extension

This is an opt-in extension family for the
[AI-DLC](https://github.com/awslabs/aidlc-workflows) workflow. It plugs into
the same `extensions/` mechanism used by the upstream `security` and
`testing` extensions, but the rules are framed for data pipelines: contracts,
schema evolution, partitioning, lineage, observability, cost controls,
backfills, and the AWS services that show up most often in this book — S3
lakehouses (Iceberg), Glue ETL, Redshift, MWAA / Step Functions, the Glue
Data Catalog / Lake Formation, and CI/CD for data assets.

When AI-DLC starts a workflow, it scans `extensions/` and presents an opt-in
prompt for each one during Requirements Analysis. If you opt **IN**, the
matching rules become **blocking constraints** — the agent will refuse to
advance a stage until verification criteria are met. If you opt **OUT**,
the full rule file is never loaded, so it costs nothing in context.

The latest version of this extension is maintained at
<https://github.com/jleto/aidlc-workflows/tree/extensions-data-engineering>.

---

## What's in the box

Seven sibling extensions. Each directory contains exactly two files:

- `<name>.md` — the rule pack (loaded only when the user opts in)
- `<name>.opt-in.md` — the opt-in prompt shown during Requirements Analysis

| Directory        | Rule prefix | Scope                                                                |
|------------------|-------------|----------------------------------------------------------------------|
| `baseline/`      | `DATAENG-*` | Platform-agnostic baseline: contracts, schema evolution, idempotency, partitioning, lateness, PII, quality gates, lineage, observability, backfills, cost, SLAs, failure semantics, reproducibility, secrets. |
| `s3-lakehouse/`  | `S3LH-*`    | S3 + Apache Iceberg / Hive tables queried by Athena, Glue, EMR, Spark, Trino, Redshift Spectrum. Layout, table format, compaction, manifest hygiene. |
| `glue-etl/`      | `GLUE-*`    | AWS Glue Spark / Spark Streaming / Ray / Python shell / Glue Studio / DataBrew as a compute target. Worker sizing, bookmarks, job parameters. |
| `redshift/`      | `RSHIFT-*`  | Redshift provisioned and Serverless. Distribution / sort keys, RA3, materialized views, federated queries, workload management. |
| `orchestration/` | `ORCH-*`    | Amazon MWAA (Airflow) and AWS Step Functions. DAG/state-machine structure, retry policy, SLA misses, secrets. |
| `catalog/`       | `CAT-*`     | Glue Data Catalog + Lake Formation: ownership, descriptions, tags, classifications, deprecation. |
| `cicd/`          | `CICD-*`    | Pull-request gates, environment promotion, schema-change diffing, smoke tests, rollback for data assets. |

All seven use the same severity model:

| Tier | Behavior |
|------|----------|
| **P0** | Blocking — the stage cannot advance until resolved. |
| **P1** | Warning at the stage that produces it; becomes blocking at Build and Test. |
| **P2** | Advisory — recorded but never blocks. |

If a rule isn't relevant to the project (e.g. `DATAENG-05` lateness when no
event ingestion is in scope), the agent marks it **N/A** with a one-line
rationale instead of treating it as a finding.

---

## How AI-DLC discovers and loads it

The core workflow does two passes at startup
(`aws-aidlc-rules/core-workflow.md`, "Extensions Loading" section):

1. **Scan** — list every subdirectory under `extensions/` and load **only**
   the lightweight `*.opt-in.md` files. Full rule files are not loaded yet.
2. **Defer** — during Requirements Analysis, present each opt-in prompt to
   the user. If they answer `A`, the full `<name>.md` rule file is loaded.
   If they answer `B`, that file is never read, saving context.

This means installing the extension is just placing the directories at the
right path. No registration step.

```
extensions/
├── data-engineering/
│   ├── README.md                          (this file)
│   ├── baseline/
│   │   ├── data-engineering-baseline.md
│   │   └── data-engineering-baseline.opt-in.md
│   ├── s3-lakehouse/
│   │   ├── s3-lakehouse.md
│   │   └── s3-lakehouse.opt-in.md
│   ├── glue-etl/
│   │   ├── glue-etl.md
│   │   └── glue-etl.opt-in.md
│   ├── redshift/
│   │   ├── redshift.md
│   │   └── redshift.opt-in.md
│   ├── orchestration/
│   │   ├── orchestration.md
│   │   └── orchestration.opt-in.md
│   ├── catalog/
│   │   ├── catalog.md
│   │   └── catalog.opt-in.md
│   └── cicd/
│       ├── cicd.md
│       └── cicd.opt-in.md
```

---

## Wiring it into each AI coding tool

The install pattern is the same for every tool: clone upstream
`awslabs/aidlc-workflows`, drop this `data-engineering/` directory under its
`extensions/` path, then point the tool at the resulting `aidlc-rules/`.
What differs per tool is **where** the agent reads rules from.

### Claude Code (Anthropic)

Claude Code reads project memory from `CLAUDE.md` and user-level memory
from `~/.claude/CLAUDE.md`. The recommended setup keeps the rules in
`~/.aidlc/aidlc-rules/` and triggers AI-DLC on a phrase.

```bash
# 1. Clone upstream AI-DLC
git clone https://github.com/awslabs/aidlc-workflows.git ~/src/aidlc-workflows

# 2. Copy this extension on top
cp -R 01_intro/aidlc-setup/aidlc-workflows/aidlc-rules/aws-aidlc-rule-details/extensions/data-engineering \
      ~/src/aidlc-workflows/aidlc-rules/aws-aidlc-rule-details/extensions/

# 3. Stage to ~/.aidlc/
mkdir -p ~/.aidlc
rm -rf ~/.aidlc/aidlc-rules
cp -R ~/src/aidlc-workflows/aidlc-rules ~/.aidlc/aidlc-rules
```

Add to `~/.claude/CLAUDE.md`:

```markdown
## AI-DLC Workflow

When the user invokes AI-DLC (e.g., "Using AI-DLC, ..."), read and follow:
@~/.aidlc/aidlc-rules/aws-aidlc-rules/core-workflow.md
```

Trigger phrase in the REPL:

```text
Using AI-DLC, treat README.md in this directory as the product intent. During
Requirements Analysis, opt IN to the data-engineering baseline, s3-lakehouse,
and orchestration extensions.
```

### Kiro CLI

Kiro reads steering rules from `.kiro/steering/` in the project, plus user-
level rules from `~/.kiro/`.

```bash
# Stage rules into the project
mkdir -p .kiro/steering/aidlc
cp -R ~/src/aidlc-workflows/aidlc-rules/* .kiro/steering/aidlc/
```

Then start Kiro from the project root (`kiro` or `kiro spec`) and refer to
the workflow by path in your initial message:

```text
Use the AI-DLC workflow at .kiro/steering/aidlc/aws-aidlc-rules/core-workflow.md.
Treat README.md as the product intent. Opt IN to data-engineering/baseline,
data-engineering/glue-etl, data-engineering/orchestration during Requirements.
```

### Codex CLI (OpenAI)

Codex reads project memory from `AGENTS.md` (and `~/.codex/AGENTS.md` for
user-level). Use the same `~/.aidlc/` install as Claude Code, then add:

```markdown
# AGENTS.md

When asked to use AI-DLC, read and follow:
~/.aidlc/aidlc-rules/aws-aidlc-rules/core-workflow.md

The data-engineering extension is at:
~/.aidlc/aidlc-rules/aws-aidlc-rule-details/extensions/data-engineering/
Opt IN per chapter using the recommendations table in
01_intro/aidlc-setup/README.md.
```

Trigger via:

```bash
codex exec "Using AI-DLC, plan a Glue ETL pipeline for this folder. Opt IN
to data-engineering/baseline and glue-etl during Requirements."
```

### Cursor CLI (`cursor-agent`)

Cursor reads project rules from `.cursor/rules/` (newer format) or
`.cursorrules` (legacy single-file). Stage AI-DLC into the rules folder:

```bash
mkdir -p .cursor/rules
cp -R ~/src/aidlc-workflows/aidlc-rules .cursor/rules/aidlc
```

Add a small `.cursor/rules/aidlc.mdc` that links into the rule pack:

```markdown
---
description: Trigger AI-DLC when the user says "Using AI-DLC, ..."
alwaysApply: false
---

When invoked, read .cursor/rules/aidlc/aws-aidlc-rules/core-workflow.md and
follow it. The data-engineering extension lives at
.cursor/rules/aidlc/aws-aidlc-rule-details/extensions/data-engineering/.
```

Then run `cursor-agent` from the project root and use the same "Using
AI-DLC, ..." phrasing.

---

## Recommended extensions per chapter

| Chapter                          | Recommended extensions                                |
|----------------------------------|-------------------------------------------------------|
| `03_datalake`                    | `baseline`, `s3-lakehouse`, `catalog`                 |
| `04_datamesh`                    | `baseline`, `catalog`, `s3-lakehouse`                 |
| `05_bigdata`                     | `baseline`, `glue-etl`, `s3-lakehouse`, `orchestration` |
| `06_orchestration`               | `baseline`, `orchestration`, `cicd`                   |
| `07_enrichment`                  | `baseline`, `glue-etl`                                |
| `09_streaming`                   | `baseline`, `glue-etl` (Streaming), `orchestration`   |
| `10_datamart/redshift_demo`      | `baseline`, `redshift`, `cicd`                        |

You can layer multiple extensions in the same workflow. Because they share
the P0/P1/P2 severity model, verification stays consistent regardless of
how many you opt into.

---

## Disabling an extension after the fact

Opt-in state is recorded by AI-DLC in `aidlc-docs/aidlc-state.md` under
`## Extension Configuration`. Toggling `Enabled: true` → `false` there is
the supported way to disable an extension mid-project — the agent rechecks
that file before enforcing any rule.

To remove an extension entirely from a fresh install, delete its directory
under `extensions/data-engineering/` before AI-DLC runs its scan. The
remaining six are independent.
