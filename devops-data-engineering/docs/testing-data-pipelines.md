# Testing data pipelines

The standard "unit + integration + end-to-end" test pyramid was designed
for stateless web services. It works for data pipelines only after some
adjustment, because the things that go wrong with pipelines are different
from the things that go wrong with HTTP handlers.

This doc lays out the four test layers a data pipeline actually needs and
where each one lives in the CI flow.

## What goes wrong with data pipelines

| Failure mode | Where it shows up | What test would have caught it |
|---|---|---|
| Wrong PySpark transformation logic | Bad numbers in the curated table | Unit |
| Schema drift in upstream API → downstream consumer breaks | Downstream Athena query fails next day | Contract |
| IAM role lacks `glue:GetTable` for the new table | Job fails on first prod run | Integration |
| Step Functions branch mishandles a retry path | Silent data duplication | Integration / smoke |
| Data quality regression — null rate spikes | Dashboard misleads users | Post-deploy DQ check + alarm |
| Prompt change makes an agent emit invalid SQL | Agent crashes or returns wrong rows | Eval (lab 9) |

Each failure mode wants a different kind of test, run at a different point
in the lifecycle. Bundle them all into "we have tests" and you'll under-
invest in the ones that catch the worst bugs.

## The four layers

### 1. Unit tests — pure logic, pure speed

**What they cover**: PySpark transformations, business rules, helper
functions, anything that takes a DataFrame in and returns a DataFrame out
without touching AWS.

**How they run**: `pytest` against a local `SparkSession`, in CI on every
push, in <60 seconds for the whole suite. No AWS credentials needed.

**The trick that matters**: structure your Glue job so the
transformation is a pure function:

```python
# glue_jobs/curate_orders.py
def transform(orders_df, customers_df):
    """Pure function. No GlueContext, no S3, no boto3."""
    return (
        orders_df
        .join(customers_df, "customer_id")
        .filter(F.col("status") != "cancelled")
        .groupBy("region").agg(F.sum("total").alias("revenue"))
    )

# main() is the thin Glue wrapper that reads, calls transform, writes.
```

Now `transform()` is unit-testable with a 10-row DataFrame in 200ms.

`reference-pipeline/glue_jobs/curate_orders.py` follows this pattern;
`reference-pipeline/tests/unit/test_curate_orders.py` is the test.

### 2. Contract tests — schemas, data shape, agreements

**What they cover**: the schema of data leaving one stage and entering the
next. The Iceberg table this job writes still has the columns the next job
reads. The JSON the API returns still matches what the ingestion job
parses.

**How they run**: as part of CI, against schema definitions stored in the
repo (or pulled from Glue Data Catalog / a contract registry).

**Tools**:

- **Great Expectations** or **Soda** for "data shape" assertions — null
  rates, value ranges, uniqueness.
- **JSON Schema** or **Avro / Protobuf** for upstream API payloads.
- The `data-product-validator` from `04_datamesh/` for DataZone-published
  contracts.

**Why this is its own layer**: schema drift is the most common cause of
overnight pipeline breakage, and it's invisible to unit tests. A unit test
on `transform()` says "given these columns, the output is correct"; a
contract test says "the columns we're given still exist."

### 3. Integration tests — real AWS, sandbox-scaled

**What they cover**: the deploy itself, IAM, service-to-service
permissions, the actual Glue catalog entries, Step Functions state
transitions, AgentCore runtime registration. Anything that involves an AWS
service responding to a real API call.

**How they run**: against a sandbox AWS account (lab 3), triggered by CI
on PR open and on push to the PR branch. Slow — expect minutes, not
seconds. They live in `reference-pipeline/tests/integration/`.

**What they look like**:

```python
# tests/integration/test_pipeline_deploys.py
def test_glue_job_runs_to_success(terraform_outputs):
    glue = boto3.client("glue")
    run_id = glue.start_job_run(
        JobName=terraform_outputs["curate_orders_job_name"],
    )["JobRunId"]
    state = wait_for_glue_job(run_id, timeout=600)
    assert state == "SUCCEEDED"

def test_curated_table_has_expected_partitions(terraform_outputs):
    athena = boto3.client("athena")
    rows = run_athena_query(
        f"SELECT count(*) FROM {terraform_outputs['curated_db']}.orders"
    )
    assert rows[0][0] > 0
```

The fixture `terraform_outputs` shells out to `terraform output -json`
against the test workspace and exposes resource ARNs. The test workspace is
torn down at the end of the run.

**Why this layer exists**: most production failures are in this band, not
in the unit-test band. IAM permissions, Step Functions retry behaviour,
Glue bookmark state — none of these are reproducible without real AWS.

### 4. Post-deploy / smoke / DQ

**What they cover**: the deploy is live, the pipeline ran once, did the
data come out right? These run *after* a successful deploy, against the
target environment.

**Three sub-types**:

- **Smoke**: the simplest possible "is the thing alive" check. Athena
  query returns rows; AgentCore endpoint returns 200; Step Functions
  history shows recent successful executions.
- **Data quality**: Deequ, Great Expectations, Soda checks against the
  output of the latest run. "The `orders` table has a non-null rate of
  >99.9% on `customer_id`." See `05_bigdata/glue-deequ-data-quality/` for
  the canonical pattern.
- **Canary**: a synthetic invocation in prod that exercises the full path.
  For an agent, this is a pinned eval question with a known-good answer
  range. For a pipeline, it's a synthetic record that flows through and
  gets verified end-to-end.

**How they run**: triggered by deploy completion. A failure rolls back —
lab 8 wires this up.

## Where each layer runs

```
                  push     PR open    PR push    PR merge   post-deploy   schedule
                   │          │          │          │            │            │
Unit               ▢ ✓        ✓          ✓          ✓                          
Contract           ▢ ✓        ✓          ✓          ✓                          
Integration                   ✓ sandbox  ✓ sandbox  ✓ staging                  
Smoke / DQ                                                       ✓ staging
                                                                 ✓ prod
Canary                                                                        ✓ prod (hourly)
```

Lab 5 shows the GitHub Actions YAML that wires this in.

## What about agent / LLM components?

LLMs and agents need their own evaluation layer because their failure modes
are different — a working but subtly wrong prompt won't show up in any of
the four layers above. Lab 9 covers:

- **Eval suites** — pinned input → expected output ranges, run on every
  prompt change (and against every candidate model upgrade).
- **Prompt-injection regression** — re-run the
  `02_security/prompt-injection-test-harness/` suite on every change to
  the system prompt or tool list.
- **Determinism fixtures** — for tests in CI, swap the live LLM for a
  recorded transcript. Real-LLM tests run on a schedule, not per-PR, to
  contain cost.

This isn't a fifth layer in the diagram above so much as a parallel track
for the agent components — but the same gating discipline applies: failing
evals block the deploy.

## How thorough is "thorough enough"?

Tempting to chase 100% coverage on the unit tier. Don't.

The empirical rule for data pipelines:

- ~70% of bugs are in the **integration** band (IAM, configuration, service
  contracts).
- ~20% are in the **contract** band (schema drift, upstream changes).
- ~10% are in the **unit** band (transformation logic).

Yet the conventional pyramid asks you to spend most of your testing budget
on units. Invert that intuition for data work: invest in fast, real-AWS
sandbox tests (lab 3 + lab 5) and good contract tests, and the unit tests
will be small but high-signal because they only need to cover the pure
transformation logic.

## When you don't have time for all four

Practical advice when you're retrofitting tests to an existing pipeline:

1. Add **contract tests** first. Cheapest to add, catches the most
   common production-breaking bug (schema drift).
2. Add **smoke / DQ in prod** second. Doesn't require you to refactor
   anything; just point Deequ at the output table and alarm on regressions.
3. Add **integration** third. Forces you to set up the sandbox in lab 3,
   which is the highest-value infrastructure investment in the chapter
   codebase.
4. Add **unit** last. Useful, but the tests above will catch more.

The labs don't enforce this order, but the reference pipeline ships with
all four so you can see the shape.
