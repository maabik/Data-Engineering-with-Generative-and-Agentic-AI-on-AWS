# Kiro Data Pipeline (Chicago Violations)

A spec-driven Glue pipeline built with [Kiro](https://kiro.dev) — the AI
IDE that drives implementation from `requirements.md` → `design.md` →
`tasks.md`. This project is the worked example for the **spec-driven
development** section of chapter 05: how an AI assistant can take a
business requirement, plan an Iceberg lakehouse pipeline, and generate
the Glue job that lands the data.

The pipeline ingests a sample of Chicago Department of Streets &
Sanitation ordinance-violation records, lands them as Iceberg tables in
the Glue catalog, and emits a curated medallion (raw → cleaned →
analytics) layout under `s3://${BUCKET}/lakehouse/`.

## Files

| Path | Purpose |
|---|---|
| `chicago_violations_pipeline.ipynb` | Companion notebook — same logic, runnable interactively for chapter walk-through. |
| `chicago-violations-pipeline/requirements.md` | The original requirements doc fed to Kiro. |
| `chicago-violations-pipeline/design.md` | The architecture/design doc Kiro produced from the requirements. |
| `chicago-violations-pipeline/tasks.md` | The implementation plan Kiro produced from the design. |
| `data/chicago-ordinance-violations-sample.csv` | Local sample (~50 rows) for end-to-end testing. |
| `steering/data-pipeline-standards.md` | Kiro **steering doc** — the standards Kiro follows when generating any pipeline (medallion layers, naming, Iceberg conventions). |
| `teardown.sh` | Deletes the Glue job, the `dq_demo_violations` Glue database (with cascade through Lake Formation if needed), and the S3 lakehouse / scripts / jars artefacts. |

## How this is meant to be read

Open the three Kiro spec files **in order** — they show the trajectory
from a one-paragraph problem statement to executable Glue code:

1. `chicago-violations-pipeline/requirements.md` — what business problem
   the data team was given.
2. `chicago-violations-pipeline/design.md` — the architecture Kiro
   proposed (S3 prefixes, Iceberg tables, Glue job shape, partitioning).
3. `chicago-violations-pipeline/tasks.md` — the step-by-step plan Kiro
   followed to generate the job script and infrastructure.
4. `steering/data-pipeline-standards.md` — the *house rules* Kiro reads
   on every prompt; the reason design.md and tasks.md converge on the
   medallion + Iceberg shape across runs.

## Run

The pipeline depends on the chapter-05 shared infrastructure
(`05_bigdata/_deploy/`): an S3 bucket `dq-demo-${ACCOUNT}-${REGION}` and
the Glue IAM role `dq-demo-glue-role`.

Once those exist:

```bash
# Stage the sample data
aws s3 cp data/chicago-ordinance-violations-sample.csv \
    "s3://dq-demo-${ACCOUNT}-${REGION}/raw/violations/sample.csv"

# Drive the pipeline interactively
jupyter notebook chicago_violations_pipeline.ipynb
```

The notebook creates the `dq_demo_violations` Glue database and writes
Iceberg tables under `s3://${BUCKET}/lakehouse/`.

## Teardown

```bash
bash teardown.sh
```

Deletes the Glue job, the `dq_demo_violations` database (with all its
Iceberg tables — the script handles Lake Formation enforcement by
self-granting `DROP`/`DESCRIBE` if the first delete fails), and the
S3 prefixes `lakehouse/`, `scripts/violations_pipeline.py`, and
`jars/iceberg-*.jar`.

**Does not** touch the shared bucket or IAM roles — those belong to the
chapter-level `_deploy/teardown-shared.sh`.
