# Data Engineering Baseline Extension

## Overview

These rules apply to any data pipeline regardless of compute or storage platform. Stack platform-specific extensions (`s3-lakehouse`, `redshift`, `postgresql`, `databricks`, `snowflake`) and capability extensions (`orchestration`, `cicd`, `catalog`) on top of this baseline.

**Enforcement**: At each applicable AIDLC stage, the model MUST verify compliance with these rules before presenting the stage completion message to the user. Findings are surfaced according to their severity tier (see below).

## Severity Tiers

Unlike most extensions where every rule is blocking, data engineering rules are assigned one of three severity tiers based on whether non-compliance is a correctness issue, a production-readiness issue, or a recommendation. The same model applies to all extensions in the `data-engineering/` family.

| Tier | Label | Enforcement |
|------|-------|-------------|
| **P0** | Blocking | Must be resolved before the stage completes. Non-compliance is a hard finding that blocks stage sign-off. |
| **P1** | Warning | Must be resolved before production go-live. Non-compliance is flagged but does not block stage completion; it becomes a hard finding at the Build and Test gate. |
| **P2** | Advisory | Good practice; record as a recommendation. Non-compliance is noted in the compliance summary but does not block any stage. |

### Blocking Finding Behavior (P0)

A **P0 finding** means:
1. The finding MUST be listed in the stage completion message under a "Data Engineering Findings" section with the rule ID, severity, and description
2. The stage MUST NOT present the "Continue to Next Stage" option until all P0 findings are resolved
3. The model MUST present only the "Request Changes" option with a clear explanation of what needs to change
4. The finding MUST be logged in `aidlc-docs/audit.md` with the rule ID, severity, description, and stage context

### Warning Finding Behavior (P1)

A **P1 finding** means:
1. The finding MUST be listed in the stage completion message under "Data Engineering Findings" with the rule ID, severity P1, and description
2. The stage MAY present "Continue to Next Stage" — P1 does not block stage sign-off
3. P1 findings carry forward and become P0 (blocking) at the Build and Test stage gate; unresolved P1 findings at Build and Test block production promotion
4. The finding MUST be logged in `aidlc-docs/audit.md`

### Advisory Finding Behavior (P2)

A **P2 finding** means:
1. The finding is noted in the stage compliance summary as a recommendation
2. No stage gate is affected
3. The finding SHOULD be logged in `aidlc-docs/audit.md` for traceability but does not require resolution

### N/A Handling

If a rule is not applicable to the current project (e.g., DATAENG-05 lateness handling when no event ingestion is in scope), mark it as **N/A** in the compliance summary with a one-line rationale. This is not a finding at any tier.

### Verification Criteria Format

Verification items in this document are plain bullet points describing compliance checks. They are distinct from the `- [ ]` / `- [x]` progress-tracking checkboxes used in stage plan files. Each item should be evaluated as compliant, non-compliant, or N/A during review.

### Precedence

When a platform-specific extension rule conflicts with a baseline rule, the stricter rule wins. The `security/baseline` extension follows the same precedence: if a SECURITY-* rule is stricter than a DATAENG-* rule, the SECURITY-* rule governs.

---

## Rule DATAENG-01: Explicit Data Contract Between Producer and Consumer

**Severity**: P0 — Blocking

**Rule**: Every dataset that crosses a process or team boundary MUST have a machine-readable data contract stored in version control. A free-text document does not satisfy this rule. The contract MUST specify all of the following fields; any omission is a blocking finding:

| Field | Requirement |
|-------|-------------|
| `schema` | Column name, data type, nullability, and semantic description for every column |
| `primary_key` | Column(s) that uniquely identify a row; `none` if append-only with explicit rationale |
| `partitioning` | Partition key(s) and grain, or `none` with rationale |
| `freshness_sla` | Maximum acceptable age of data at the consumption layer (e.g., `T+4h`, `daily by 08:00 UTC`) |
| `volume_expectation` | Expected row count range per partition or per day; used to set DATAENG-09 volume monitors |
| `value_constraints` | Allowed enums, ranges, or regex patterns for critical columns; at least one per non-free-text critical column |
| `classification` | Sensitivity per column per DATAENG-06 (`public`, `internal`, `confidential`, `pii`, `phi`, `pci`) |
| `version` | Semantic version (`MAJOR.MINOR.PATCH`); breaking changes increment MAJOR per DATAENG-02 |
| `owner` | Owning team identifier per CAT-01 |
| `consumers` | Named downstream pipelines, services, or teams; used for breaking-change notification per DATAENG-02 and deprecation notice per CAT-04 |
| `contract_enforcement` | Tool and mode used to validate the contract at runtime (see tooling guidance below) |

Implicit contracts ("whatever the upstream table looks like today") are prohibited for any dataset consumed by another pipeline, service, or analytical workload.

**Contract Format**: The contract MUST be expressed in one of the following machine-readable formats. Free-text Markdown or Word documents do not qualify:
- **dbt model contract** (`models/<name>.yml` with `contract: {enforced: true}` and `columns:` block) — preferred for dbt projects
- **Soda contract** (`contracts/<name>.yml` using the Soda Contract specification) — preferred when dbt is not the transformation layer
- **OpenAPI/JSON Schema** (`data-contracts/<name>.json` or `.yaml`) — acceptable for API-style datasets
- **AsyncAPI + Schema Registry subject** — required for streaming sources (see tooling guidance below); a schema registry subject name MUST be recorded in the contract file

**Contract Enforcement Tooling**:

*Batch and SQL pipelines*:
- `dbt model contracts` — enforces schema shape at model materialization time; `contract: {enforced: true}` causes dbt to fail if the produced relation does not match the declared columns and types
- `Soda Contracts` — enforces column presence, type, nullability, and value constraints as a post-write check; integrates with CI and orchestration
- Custom assertion libraries (Great Expectations, Deequ) are acceptable for constraint validation but MUST be paired with a machine-readable schema definition file (JSON Schema or equivalent) for the schema fields

*Streaming pipelines*:
- **AWS Glue Schema Registry** — required when the streaming transport is Amazon Kinesis, Amazon MSK, or AWS Glue Streaming ETL; schema subject name and registry ARN MUST be recorded in the contract file; compatibility mode MUST be set to `BACKWARD` or `FULL` per DATAENG-02
- **Confluent Schema Registry** — required when the streaming transport is Confluent Cloud Kafka or self-managed Kafka; subject naming strategy (TopicNameStrategy, RecordNameStrategy) MUST be documented; compatibility mode MUST be set to `BACKWARD` or `FULL`
- Schema registry enforcement mode MUST be `FULL` for any topic with multiple consumer groups — `BACKWARD`-only does not protect producers from breaking consumers with older schemas

**Verification**:
- Requirements Analysis includes a `data-contracts/` directory in the repository containing one contract file per dataset produced or consumed across the unit boundary, in one of the approved formats above.
- A free-text document, diagram, or Confluence page alone does not constitute a contract — a machine-readable file MUST exist.
- Functional Design references the contract file path for each dataset; the column list in the design is derived from the contract, not independently authored.
- Code Generation wires the declared `contract_enforcement` tool into the pipeline execution path: dbt contract enforcement at model run, Soda check at post-write step, schema registry serializer/deserializer at producer and consumer.
- For streaming sources: Code Generation configures the producer serializer to register schemas against the named registry subject; consumer deserializer is configured to validate on read.
- Build and Test includes a deliberate contract violation test: a column is removed or a type is narrowed in a test fixture and the enforcement tool is asserted to fail the run.
- A contract is versioned; the MAJOR version in the contract file matches the major version of the deployed schema.

---

## Rule DATAENG-02: Backward-Compatible Schema Evolution

**Severity**: P0 — Blocking

**Rule**: Schema changes to existing datasets MUST be backward compatible by default. Adding a nullable column is allowed; removing a column, renaming a column, narrowing a type, or changing nullability from nullable to non-nullable is a breaking change that requires either (a) a major version bump of the data contract with a coexistence window where both versions are produced, or (b) explicit consumer sign-off captured in the requirements artifact.

**Verification**:
- Brownfield reverse engineering captures the current schema for every persisted dataset.
- Any proposed schema change is classified in the design as "additive" or "breaking" with rationale.
- Breaking changes have an entry in the requirements artifact naming each downstream consumer and the coexistence plan.
- Code Generation produces migration scripts (DDL or table-format schema evolution operations) rather than implicit type coercion.

---

## Rule DATAENG-03: Idempotent and Re-runnable Transformations

**Severity**: P0 — Blocking

**Rule**: Every batch or micro-batch transformation MUST be safely re-runnable for any given input partition or time window without producing duplicates, leaving the target in a corrupted state, or requiring manual cleanup. Append-only patterns without dedup keys, INSERT without UPSERT/MERGE semantics on tables with declared primary keys, and "delete-then-insert" patterns without transactional guarantees are prohibited.

**Verification**:
- Functional Design names the dedup key or merge condition for each write.
- Code Generation uses MERGE/UPSERT, transactional overwrites of partitions, or table-format snapshot replacement — never naive INSERT into a keyed table.
- Build and Test includes a test case that runs the same partition twice and asserts row counts and content are stable.
- Streaming pipelines document at-least-once vs exactly-once semantics and the downstream deduplication mechanism if at-least-once.

---

## Rule DATAENG-04: Deterministic Partitioning Strategy

**Severity**: P1 — Warning

**Rule**: Every persisted dataset larger than 10 GB or expected to grow past it MUST have an explicit partitioning strategy chosen against the actual query and write patterns. High-cardinality natural keys (user_id, order_id, uuid) MUST NOT be used as partition keys. Partition keys are derived from columns present in the data; partition-pruning predicates MUST be expressible by typical consumers.

**Verification**:
- Functional Design specifies partition keys and rationale (read patterns, write patterns, file count targets).
- Estimated partition count over a one-year horizon is within platform-appropriate limits (no more than ~10,000 partitions for Hive-style; Iceberg/Delta hidden partitioning preferred where supported).
- Code Generation produces partition writes aligned to the design — no accidental over-partitioning by including timestamps with second-level resolution.
- For time-based partitioning, the grain (day/hour) is justified by query SLA and ingestion volume.

---

## Rule DATAENG-05: Late-Arriving and Out-of-Order Data Handling

**Severity**: P1 — Warning

**Rule**: Every pipeline that ingests event data MUST define a policy for late-arriving records, including: maximum acceptable lateness, how late records are merged into existing partitions, whether they trigger downstream reprocessing, and how watermarks or processing-time vs event-time semantics are handled.

**Verification**:
- Requirements Analysis captures the lateness SLA (e.g., "events up to 7 days late are processed in place; older records are routed to a quarantine table").
- Functional Design specifies the merge mechanism (partition rewrite, MERGE on event_time + key, etc.).
- Code Generation implements quarantine routing and writes a count of late records to an observability sink.
- Tests include a late-record scenario.

---

## Rule DATAENG-06: PII and Sensitive Data Classification

**Severity**: P0 — Blocking

**Rule**: Every column containing personally identifiable information, payment data, protected health information, or other regulated content MUST be classified in the data contract. Classified columns MUST be protected via at-rest encryption with customer-managed keys, masked or tokenized at the consumption layer for non-authorized roles, excluded from non-production environments unless synthetic, and logged in an access audit trail.

**Verification**:
- The data contract has a `classification` field per column (e.g., `public`, `internal`, `confidential`, `pii`, `phi`, `pci`).
- NFR Design includes a key management and masking strategy for classified columns.
- Code Generation does not log values of classified columns; column-level grants or masking policies are applied.
- Test fixtures use synthetic values for classified columns or are explicitly marked as anonymized.
- This rule composes with the `security/baseline` extension; if SECURITY-* rules conflict, the stricter rule wins.

---

## Rule DATAENG-07: Data Quality Gates with Explicit Expectations

**Severity**: P0 — Blocking

**Rule**: Every pipeline MUST run data quality checks against produced datasets and either fail the pipeline, quarantine bad records, or emit an alert on violation — never silently produce bad data downstream. Minimum expectations: row count within tolerance band, primary key uniqueness, non-null on contract-required columns, referential integrity on declared foreign keys, and at least one domain check per critical column.

**Verification**:
- Functional Design lists expectations per dataset with action-on-failure (fail/quarantine/alert).
- Code Generation uses a declarative framework (e.g., Great Expectations, Deequ, DLT expectations, dbt tests, custom assertions) rather than ad-hoc IF statements.
- Tests cover both pass and fail paths for at least one expectation per critical dataset.
- Quality results are persisted to a queryable location for trend analysis.

---

## Rule DATAENG-08: Column-Level Lineage Captured End-to-End

**Severity**: P1 — Warning

**Rule**: For every produced dataset, the pipeline MUST emit lineage metadata capturing: source tables and columns, transformations applied, the producing job and run identifier, and the timestamp of production. Lineage MUST be queryable without re-reading source code.

**Verification**:
- Infrastructure Design names the lineage capture mechanism (OpenLineage, Marquez, Unity Catalog, Snowflake ACCESS_HISTORY, Glue Crawler + Lake Formation, dbt artifacts, etc.).
- Code Generation emits lineage events at write boundaries or relies on a platform feature that does so automatically.
- Build and Test verifies at least one end-to-end lineage trace from a leaf source through to a published mart.

---

## Rule DATAENG-09: Pipeline Observability — Monitors Exist and Alert

**Severity**: P1 — Warning

**Rule**: Every production dataset MUST have active monitors on four pillars: freshness (time since last successful write), volume (row count and byte count deviation from expected band), schema (drift vs the contract), and quality (DATAENG-07 results). Each monitor MUST be wired to a notification or paging mechanism with a defined owner. Silent dashboards — monitors that display but never alert — do not satisfy this rule. SLA targets that the monitors measure against are defined separately in DATAENG-12.

**Verification**:
- Infrastructure Design names the monitoring stack (Monte Carlo, Soda, custom CloudWatch/Datadog, etc.), the four pillars covered per dataset, and the on-call routing for each alert.
- Code Generation or IaC provisions monitors alongside the pipeline — not as a separate post-launch manual step.
- Build and Test includes a synthetic violation (e.g., write suppressed to trigger a freshness alert) that fires and resolves a monitor, confirming the alerting path is wired end-to-end.

---

## Rule DATAENG-10: Backfill Strategy Separate From Incremental

**Severity**: P1 — Warning

**Rule**: Pipelines MUST distinguish between incremental processing and backfill, with separate parameterization, separate resource sizing, and a documented backfill procedure. A backfill MUST NOT block ongoing incremental processing, and MUST be safely re-runnable per DATAENG-03.

**Verification**:
- Functional Design includes a backfill section: parameter shape (date range, partition list), resource profile, expected duration, isolation from incremental runs.
- Code Generation produces parameterized entry points for both modes; no hard-coded "last 1 day" filters in code that also needs to backfill years.
- Build and Test includes a backfill scenario over multiple partitions.

---

## Rule DATAENG-11: Cost Controls and Resource Caps

**Severity**: P1 — Warning

**Rule**: Every pipeline MUST have explicit upper bounds on resource consumption: maximum concurrent compute, maximum runtime, maximum bytes scanned or processed per run, and a credit/dollar budget where the platform supports it. Unbounded autoscaling is prohibited for production workloads.

**Verification**:
- NFR Requirements names per-pipeline budgets in compute units (DBU, credit, slot-hours, DPU-hours) and dollars.
- Infrastructure Design configures resource monitors, query timeouts, max cluster size, or warehouse auto-suspend per platform conventions.
- Code Generation includes guardrails (query timeout, max records, LIMIT on exploratory reads, partition pruning predicates).
- Cost attribution metadata (cost center, project, environment tags) is applied to every compute resource.

---

## Rule DATAENG-12: Explicit SLA, SLO, and SLI Per Pipeline

**Severity**: P1 — Warning

**Rule**: Every production pipeline MUST have written SLAs (the consumer-facing commitments), SLOs (the internal measurable targets derived from SLAs), and SLIs (the specific metric or query that measures each SLO) defined before go-live. Required SLA dimensions: freshness (P50 and P99 delivery time), completeness (minimum acceptable row fraction), and accuracy (acceptable error rate per DATAENG-07). An SLA without a corresponding SLI query that can be evaluated from existing telemetry is not acceptable. Monitoring that enforces these targets is defined separately in DATAENG-09; this rule governs that the targets themselves are written down and computable.

**Verification**:
- Requirements Analysis captures the consumer-facing SLA per dataset (what the producing team promises).
- NFR Requirements translates each SLA dimension into a measurable SLO with a numeric target (e.g., "data available by 08:00 UTC on 99.5% of days").
- NFR Requirements defines the SLI for each SLO: the specific metric, query, or monitor output used to evaluate it (e.g., `max(load_timestamp) < now() - interval '26 hours'` evaluated against the freshness monitor).
- Build and Test verifies each SLI can be evaluated from existing telemetry and produces a pass/fail result against its SLO threshold.

---

## Rule DATAENG-13: Failure Semantics Documented and Tested

**Severity**: P0 — Blocking

**Rule**: Every pipeline MUST declare its failure semantics: what happens on a transient error (retry policy with backoff and max attempts), what happens on a permanent error (dead-letter behavior, partial commit policy), and what guarantees the consumer receives (at-most-once, at-least-once, exactly-once and the mechanism). Silent retries that hide data loss are prohibited.

**Verification**:
- Functional Design includes a "failure modes" section per pipeline.
- Code Generation implements explicit retry with bounded attempts and dead-letter routing for permanent failures.
- Tests include induced-failure scenarios for at least one transient and one permanent failure mode.

---

## Rule DATAENG-14: Reproducibility — Deterministic Outputs and Pinned Dependencies

**Severity**: P1 — Warning

**Rule**: A pipeline re-run against the same input data MUST produce byte-identical or semantically identical output, modulo declared non-deterministic columns (e.g., load_timestamp). Dependencies (libraries, container images, model weights, reference data versions) MUST be pinned. Re-runs of historical periods MUST be possible without "the dataset I had in March no longer exists" failures.

**Verification**:
- Code Generation pins all runtime dependencies (lockfile, image digest, library versions).
- Reference data snapshots are taken at known-good points and addressable by version.
- Non-deterministic columns are enumerated in the data contract.
- Build and Test includes a determinism check: re-run produces the same row hashes.

---

## Rule DATAENG-15: Secrets and Connection Credentials Never in Code

**Severity**: P0 — Blocking

**Rule**: Database connection strings, API tokens, cloud credentials, service principals, and any secret used by the pipeline MUST be sourced at runtime from a managed secret store (AWS Secrets Manager, AWS Systems Manager Parameter Store, HashiCorp Vault, Azure Key Vault, GCP Secret Manager, Databricks secret scopes, Snowflake EXTERNAL ACCESS integrations). Secrets MUST NOT appear in code, config files committed to git, notebooks, environment files, container images, audit logs, error messages, stack traces, or DAG definitions. Rotation MUST be possible without redeploying the pipeline.

**Verification**:
- Code Generation reads secrets via the secret store SDK; no string literals or env-from-file patterns for production secrets.
- Infrastructure Design specifies IAM/role-based access to secrets and rotation cadence.
- Repository scanning (pre-commit, secret detection) is mandated in the build instructions.
- This rule composes with `security/baseline`; secret-handling rules from SECURITY-* take precedence where stricter.
