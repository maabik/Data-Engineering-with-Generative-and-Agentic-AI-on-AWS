# Lab 5 — Pipeline testing strategy

The four-layer testing model — unit, contract, integration, smoke/DQ —
is described in
[`../../docs/testing-data-pipelines.md`](../../docs/testing-data-pipelines.md).
This lab is the operational version: which test runs when, the GitHub
Actions YAML that wires each layer in, and the patterns that keep the
suite fast enough to actually run.

## Where each layer lives in the reference pipeline

| Layer | Location | Run trigger |
|---|---|---|
| **Unit** | [`reference-pipeline/tests/unit/`](../../reference-pipeline/tests/unit/) | every push, every PR |
| **Contract** | [`contracts/`](contracts/) (this lab) | every push, every PR |
| **Integration** | [`reference-pipeline/tests/integration/`](../../reference-pipeline/tests/integration/) | PR open / push to PR branch (sandbox-scoped) |
| **Smoke / DQ** | [`smoke/`](smoke/) (this lab) | post-deploy, in target environment |

The unit tier is already in the reference pipeline. This lab adds the
other three.

---

## 1. Contract tests

Two flavors are useful in practice:

### a) Schema contracts via JSON Schema

`contracts/schemas/orders.schema.json` defines what the upstream API
*promises* to return. The ingestion test runs sample API payloads through
the schema and fails if any drift.

```bash
pip install jsonschema
python -m pytest labs/05-pipeline-testing-strategy/contracts/
```

Real-world variant: pull the schema from a registry (Confluent, AWS Glue
Schema Registry) instead of from the repo, so producers and consumers
share a single source of truth.

### b) Data-shape contracts via Great Expectations

`contracts/expectations/region_revenue.yaml` defines:

- non-null rate ≥ 99.9% on `region`
- `revenue` ≥ 0 always
- distinct `region` count between 5 and 50

The check runs against the curated table after a sandbox deploy. CI
fails the PR if the curated output doesn't satisfy the contract — even
if the integration test "succeeded" because it didn't assert on shape.

## 2. Integration tests

Already covered in `reference-pipeline/tests/integration/`. The
operational considerations:

- **Run them against the PR's sandbox**, not against staging. Lab 3's
  workspace-per-PR pattern is what makes that affordable.
- **Cap them with a timeout.** A misbehaving Glue job can hang forever;
  set `pytest --timeout=900` and let CI fail hard.
- **Keep state isolated.** The sandbox is per-PR; integration tests can
  freely write/delete data because nobody else uses that workspace.
- **Don't mock anything.** The whole point of this layer is to exercise
  the real services. If a test wants a mock, it belongs in unit.

## 3. Post-deploy smoke / DQ

After a successful deploy to staging or prod, run:

- **Smoke** — `smoke/test_smoke.py`: the state machine has a
  recently-succeeded execution; the curated table is queryable; the
  AgentCore endpoint responds 200.
- **DQ** — `smoke/run_dq.py`: Deequ checks against the curated table.
  Wires into the `02_security/finops/` budget tooling so DQ failures can
  trigger a circuit breaker on Bedrock invocations.

CI runs these from a step keyed on a successful deploy. Failure
**triggers rollback** — see lab 8 for the rollback wiring.

---

## When to invest in which layer

The advice in [`../../docs/testing-data-pipelines.md`](../../docs/testing-data-pipelines.md):

1. Contract first (cheapest, most bugs caught).
2. Smoke / DQ second (no refactor required).
3. Integration third (requires lab 3 to be set up).
4. Unit last.

Do them in that order if you're retrofitting tests.

---

## Files in this lab

- [`contracts/schemas/orders.schema.json`](contracts/schemas/orders.schema.json)
- [`contracts/test_orders_contract.py`](contracts/test_orders_contract.py)
- [`contracts/expectations/region_revenue.yaml`](contracts/expectations/region_revenue.yaml)
- [`smoke/test_smoke.py`](smoke/test_smoke.py)
- [`smoke/run_dq.py`](smoke/run_dq.py)

---

## Common pitfalls

- **"Fast" unit tests that import boto3.** If a unit test imports boto3,
  the boto3 client init alone adds 200ms+ — multiply by 50 tests and the
  suite is slow. Only import inside the function under test.
- **Sharing fixtures between integration tests.** Tempting (Glue cold
  starts hurt). Don't — flaky tests create state that breaks unrelated
  tests. Each test cleans up after itself.
- **Asserting on row counts that are "≈ X".** Pipelines drift; an exact
  count that worked yesterday fails tomorrow. Assert on shape (non-null
  rate, value range), not on exact magnitude, unless the magnitude itself
  is the contract.
- **Skipping DQ in sandbox.** If staging fails DQ, you want to have seen
  it in sandbox. Run the same DQ check against the per-PR sandbox at the
  end of integration.
