# AWS Glue ETL Extension

Rules for data engineering workloads that use AWS Glue as a compute surface — Spark ETL jobs, Spark Streaming ETL jobs, Ray jobs, Python shell jobs, interactive sessions, and visually authored jobs (Glue Studio, DataBrew). Applies whenever Glue is the runtime for transformation, ingestion, or movement of data, regardless of source or destination.

This extension layers on top of `data-engineering/baseline`. It composes with `data-engineering/catalog` (which mandates Glue Data Catalog as a registration target), `data-engineering/s3-lakehouse` (which mandates Glue Data Catalog as the lakehouse metastore in S3LH-02), `data-engineering/cicd` (which mandates Glue job scripts in version control in CICD-01 and provenance tagging in CICD-09), `data-engineering/orchestration` (which governs Step Functions and MWAA invocation of Glue), and `data-engineering/redshift` (which mandates JDBC for Glue ETL → Redshift in RSHIFT-11).

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule GLUE-01: Job Type and Glue Version Chosen Deliberately

**Severity**: P1 — Warning

**Rule**: Every Glue job MUST declare its job type and Glue version with documented rationale. The job type — Spark, Spark Streaming, Ray, or Python shell — fundamentally determines runtime cost, parallelism model, and available APIs. Defaulting to Spark for everything wastes money on workloads that fit in a Python shell; defaulting to Python shell starves jobs that need a distributed engine.

**Job type selection criteria**:

| Job type | Use when | Avoid when |
|----------|----------|------------|
| **Spark (Glue ETL)** | Batch transforms over >10 GB per run, joins across distributed datasets, writes to Iceberg/Delta tables | Source/sink fits in memory of a single worker; the work is sequential and not partitionable |
| **Spark Streaming** | True streaming sources (Kinesis Data Streams, MSK, self-managed Kafka) with continuous ingestion | Micro-batch over an hourly schedule — that's a Spark batch job triggered hourly, not streaming |
| **Ray** | Distributed Python workloads that are model-training-adjacent or require fine-grained Python parallelism rather than Spark's bulk-synchronous model | General ETL — Spark is the better fit and has more mature catalog, bookmark, and connector integration |
| **Python shell** | Small jobs under 1 DPU: orchestration glue, single-file landings, sub-GB transformations, calls to external APIs | Anything that needs distributed compute or reads more than tens of millions of rows |

**Glue version pinning**:

- Glue version (4.0, 5.0, etc.) MUST be pinned explicitly per job — not left to "latest." Glue versions bundle a specific Spark, Python, and connector matrix; an unpinned job picks up new versions at AWS's discretion and breaks reproducibility.
- End-of-support windows for older Glue versions MUST be tracked. A Glue 3.0 job still in production after the documented end-of-support date is a P0 finding at the next review — schedule the upgrade before the deadline, not after the runtime stops.
- When upgrading Glue versions, the upgrade MUST be tested on a non-production job run with representative data volume; behavior changes between Glue versions (Spark API deprecations, connector defaults, Python library versions) are routine and assumed-compatible upgrades have failed silently in production.

**Verification**:
- Functional Design names the job type per Glue job with rationale tied to the data volume, parallelism need, and source/sink characteristics.
- Infrastructure Design lists the pinned Glue version per job and the end-of-support date for that version.
- Code Generation sets `GlueVersion` explicitly in the job definition; no job uses an unset or wildcard version.
- Build and Test for any Glue version upgrade includes a parallel run on a representative dataset comparing output row counts and a sample of values against the prior version.

---

## Rule GLUE-02: Spark DataFrame Is the Default — DynamicFrame Only With Cause

**Severity**: P2 — Advisory

**Rule**: Spark DataFrame is the default API for Glue Spark jobs. DynamicFrame MUST be used only when its specific capabilities are needed: schema inference from heterogeneous sources, `resolveChoice` to handle columns with mixed types across files, `relationalize` to unnest JSON, or reading from a catalog table whose schema is genuinely unknown at job-author time. Code that converts DynamicFrame to DataFrame on the first line and never uses DynamicFrame methods is a code smell — start with the DataFrame API instead.

**Why DataFrame by default**:

- DataFrame is the dominant Spark API; it has more documentation, more community examples, more performance tuning guidance, and more compatibility with non-Glue Spark environments. Code written against DataFrame ports to EMR, Databricks, and self-managed Spark; code written against DynamicFrame is Glue-specific.
- DynamicFrame's schema flexibility carries CPU cost. Every record passes through Glue's choice-resolution machinery even when the schema is in fact uniform. For uniform-schema datasets, DataFrame is faster and produces cleaner Spark plans.
- Catalyst optimization is more thoroughly applied to DataFrame operations. Several DynamicFrame operations are opaque to Catalyst and force whole-stage codegen to bail out.

**When DynamicFrame is the right choice**:

- Reading semi-structured data (JSON, log lines) where individual records have different shapes and column types vary across files. `resolveChoice` consolidates these without failing the job.
- Reading from a catalog table whose underlying files have evolved through multiple schema versions and whose Spark-inferred schema would conflict.
- One-time data exploration and profiling jobs where author time is more valuable than runtime cost.

**Verification**:
- Functional Design states the chosen API per job (DataFrame or DynamicFrame) with rationale.
- Code Generation reviews flag any job that imports `awsglue.dynamicframe` but immediately calls `toDF()` without invoking a DynamicFrame-specific method — convert it to a DataFrame-native job.
- Mixed-type columns or schema inconsistencies that justified DynamicFrame are documented in the design as the source data's known shape, not as defensive coding.

---

## Rule GLUE-03: Job Bookmarks Used for Incremental Sources

**Severity**: P0 — Blocking

**Rule**: Glue jobs reading from incremental sources (S3 prefixes that grow over time, JDBC tables with monotonic columns, catalog tables backed by partitioned data) MUST use Glue job bookmarks to track processed data. Re-processing the entire source on every run is acceptable only for jobs explicitly designed as full-snapshot pipelines, and that design choice MUST be documented. Disabling bookmarks (`--job-bookmark-option job-bookmark-disable`) in production for an incremental source is prohibited.

**Bookmark requirements**:

- The job script MUST call `job.init(args['JOB_NAME'], args)` at start and `job.commit()` at end. A job that omits `job.commit()` reads the same data every run because the bookmark never advances. This is the single most common Glue job defect and is not caught by any AWS-side guardrail.
- The bookmark option MUST be set per job: `job-bookmark-enable` for incremental, `job-bookmark-disable` for full-snapshot (with documented rationale), `job-bookmark-pause` for one-off reprocessing windows. `pause` MUST be reverted to `enable` after the reprocessing window — leaving a job in `pause` indefinitely silently disables bookmarks.
- Bookmark behavior depends on source type. For S3 sources, bookmarks track which files have been read; for JDBC, they track the maximum value of the bookmark key column. The chosen bookmark key for JDBC sources MUST be monotonically increasing and indexed at the source.
- DynamicFrame-only feature: bookmarks work natively on DynamicFrame reads from catalog tables and from `from_options`. DataFrame reads from S3 do NOT participate in Glue bookmarks unless the read is wrapped through `glueContext.create_dynamic_frame.from_catalog` or equivalent. Jobs using pure DataFrame APIs against incremental sources MUST implement watermark tracking explicitly (e.g., maximum processed timestamp written to a control table) — this is a design decision that MUST be made and documented, not discovered when re-runs duplicate data.

**Bookmark reset procedure**:

- Resetting a bookmark (e.g., to reprocess a backfill window) MUST follow a documented runbook: stop downstream consumers or write to a side branch, run with `--job-bookmark-option job-bookmark-pause` to test, then `--job-bookmark-option job-bookmark-from-time <RFC3339>` for the reprocessing run, then restore to `job-bookmark-enable`. Resetting in production without this sequence has caused duplicate writes to downstream tables.
- The reset event MUST be logged in audit.md with the reset reason, the time window reprocessed, and the operator who ran it.

**Verification**:
- Functional Design states per source whether the read is incremental or full-snapshot, and the bookmark key column for JDBC sources.
- Code Generation reviews confirm `job.init` and `job.commit` are present in every Glue Spark job; the absence of either is a P0 finding.
- For DataFrame-based jobs reading incremental sources, the design specifies the explicit watermark tracking mechanism (control table, max-timestamp file in S3, etc.).
- Build and Test includes a re-run test: invoke the job twice on the same incremental source and assert the second run processes zero or only newly-added rows.
- The bookmark reset runbook is included in the build-and-test runbook section.

---

## Rule GLUE-04: Worker Type, DPU Count, and Auto Scaling Sized to Measured Workload

**Severity**: P1 — Warning

**Rule**: Worker type, number of workers, and Auto Scaling configuration MUST be chosen against measured data volume, partition count, and shuffle behavior — not picked from a default. The wrong worker type is the single biggest contributor to Glue job cost overruns: undersized G.1X workers spill to disk and run for hours; oversized G.4X workers sit idle and bill at premium rates.

**Worker type selection**:

| Worker type | DPUs | Memory per worker | Use when |
|-------------|------|-------------------|----------|
| **G.025X** | 0.25 | 4 GB | Streaming jobs with very low throughput; small Python shell-equivalent loads where Spark is still preferred for catalog integration |
| **G.1X** | 1 | 16 GB | Default for most batch ETL — covers the broad middle of workloads |
| **G.2X** | 2 | 32 GB | Heavy joins, large shuffles, jobs that spill on G.1X. Verify spill before upgrading; a slow G.1X job is not always memory-bound |
| **G.4X** | 4 | 64 GB | Memory-intensive transforms (large broadcast joins, wide aggregations on TB-scale data). Required only when G.2X demonstrably spills |
| **G.8X** | 8 | 128 GB | Reserved for documented memory-pressure cases. Unjustified G.8X selection is the most common cost finding in Glue reviews |
| **R-series (R.1X, R.2X, R.4X, R.8X)** | Memory-optimized variants of G-series | Use when measured spill is heavy but CPU is underutilized; rare |

**DPU and Auto Scaling**:

- Number of workers MUST be set as a function of input partition count and target task parallelism — not picked as a round number. Rule of thumb: target 2–4 Spark tasks per executor core. A job reading 200 input partitions with G.1X workers (4 cores each) is well-served by 13–25 workers; running 50 workers leaves most idle.
- Auto Scaling MUST be enabled for jobs with variable input volume; the maximum worker count MUST be capped to bound cost runaway (composes with DATAENG-11). Leaving Auto Scaling at the account-default maximum is not a sizing decision.
- Streaming jobs MUST set the worker count statically — Auto Scaling on streaming ETL is not supported as of Glue 4.0 and producing a job definition that assumes it will scale is a defect.
- The DPU-hour budget per job run MUST be projected before the job goes to production. A job that consumes 80 DPU-hours per run multiplied by hourly schedules is several thousand dollars per month — and is invisible until the bill arrives.

**Verification**:
- NFR Requirements captures expected input volume, partition count, and run frequency per job.
- Infrastructure Design specifies worker type, baseline worker count, Auto Scaling enabled/disabled, and max workers — with rationale referencing the volume estimate.
- Build and Test runs the job at representative volume and captures actual DPU-seconds consumed; if actual usage diverges from the projection by more than 50%, the worker count or type is reassessed before production.
- Cost projection covers steady-state monthly DPU-hour cost per job and is signed off as part of NFR Requirements.

---

## Rule GLUE-05: Job Parameters and Secrets Bound Through Defined Mechanisms

**Severity**: P0 — Blocking

**Rule**: Glue job arguments MUST follow defined patterns for configuration, secrets, and Glue-managed flags. Plaintext credentials in Default Arguments are prohibited. Required Glue feature flags MUST be set explicitly. Tuning parameters carried as job arguments MUST be parameterized — not hardcoded inside the job script.

**Required Glue feature flags**:

The following arguments MUST be set on every applicable job. Omission is a P0 finding because each flag enables a feature that downstream rules depend on:

- `--enable-glue-datacatalog`: Required for any job that reads from or writes to a catalog table. Without it, Spark uses its own in-memory metastore and silently bypasses the Glue Data Catalog (defeats S3LH-02 and CAT-02).
- `--enable-continuous-cloudwatch-log`: Required for all production Spark jobs (composes with GLUE-08).
- `--enable-metrics`: Required for all production Spark jobs (composes with GLUE-08).
- `--enable-job-insights`: Required for all production Spark jobs (composes with GLUE-08).
- `--job-bookmark-option`: Required to be set explicitly to `enable`, `disable`, or `pause` per GLUE-03; an unset value defaults to `disable` silently.
- `--enable-spark-ui` and `--spark-event-logs-path`: Required for jobs expected to run more than 5 minutes; the Spark UI history is the primary post-incident debugging surface (composes with GLUE-08).

**Secrets**:

- Database passwords, API keys, and any other credential MUST resolve through Secrets Manager — not be passed as job arguments and not be embedded in the job script (composes with DATAENG-15).
- The job's IAM role MUST have `secretsmanager:GetSecretValue` scoped to specific secret ARNs — not `secretsmanager:*` on `*`. Cross-account secret access MUST be explicitly justified.
- Connection objects (the Glue Connection resource type) that bundle credentials with network configuration MUST reference Secrets Manager rather than storing the password in the connection definition itself. Older Glue Connection objects with embedded passwords MUST be migrated.

**Tuning parameters**:

- Spark configuration overrides (`--conf spark.sql.shuffle.partitions=...`) MUST be passed as job arguments rather than hardcoded with `spark.conf.set` inside the script. This makes tuning visible in the job definition and CI-reviewable.
- Job-specific input paths, output paths, and run dates MUST be passed as job arguments — not hardcoded. Hardcoded paths defeat reuse across environments and force per-environment script copies (composes with CICD-01).

**Verification**:
- Code Generation reviews list the job arguments per job; the required Glue feature flags above are present and any omission is justified in writing.
- Infrastructure Design specifies the IAM role for each job with `secretsmanager:GetSecretValue` scoped to specific ARNs and lists the secret ARNs the job consumes.
- No Glue Connection object in the design has an inline password.
- Static analysis or grep checks confirm no `spark.conf.set("spark.sql.*")` or hardcoded credential string in committed job scripts.

---

## Rule GLUE-06: Pushdown Predicates and Partition Pruning Enforced

**Severity**: P1 — Warning

**Rule**: Glue jobs reading from catalog tables backed by partitioned data MUST use pushdown predicates so the job reads only the partitions it needs. Reading an entire partitioned table when the job filters to a single date partition wastes runtime and S3 GET cost. Pushdown failures are not always visible from the job script — they manifest as long read times and high DPU-second consumption that look like compute problems but are actually I/O problems.

**Mechanisms**:

- For DynamicFrame reads via `glueContext.create_dynamic_frame.from_catalog`, partition pruning uses the `push_down_predicate` argument. The predicate is a SQL expression evaluated against partition columns. Without it, all partitions are listed.
- For partitions that are themselves the result of an expensive partition discovery (high-cardinality date or hour partitions), `catalogPartitionPredicate` is preferred over `push_down_predicate` — it is evaluated server-side by the Glue Data Catalog before partition metadata is fetched, avoiding the listing cost entirely.
- For DataFrame reads via Spark SQL or `spark.read.format(...).table(...)`, partition pruning happens through Catalyst when the WHERE clause references partition columns directly. Joins and subqueries that obscure the partition column from the planner defeat pruning; the design MUST verify the plan, not assume Catalyst will figure it out.
- For Iceberg tables (composes with S3LH-04), partition transforms (`bucket`, `truncate`, `days`) work correctly only when the query references the underlying column; predicates on derived columns will not prune. Iceberg table designs MUST document which query predicates align with which partition transforms.

**Verification**:
- Functional Design lists per source the partition columns and the expected query predicate per job run.
- Code Generation reviews show `push_down_predicate` or `catalogPartitionPredicate` set on every DynamicFrame read from a partitioned catalog table; jobs without one are flagged.
- Build and Test captures a Spark plan (`EXPLAIN`) for representative jobs and confirms partition filters appear in the scan node — not as a post-scan filter.
- Glue job metrics MUST show partition reads bounded to the expected count; a job that reads 365 partitions when filtering to one day is a finding regardless of correctness.

---

## Rule GLUE-07: Retry, Timeout, and Concurrent Run Controls Set Explicitly

**Severity**: P0 — Blocking

**Rule**: Every Glue job MUST set explicit values for `Timeout`, `MaxRetries`, and `MaxConcurrentRuns`. The defaults — 2880-minute timeout, 0 retries, 1 concurrent run — are not safe defaults for most workloads: 48 hours is far too long for cost protection on a misbehaving job; zero retries gives no resilience to transient failures; and 1 concurrent run will silently queue overlapping invocations from upstream orchestrators.

**Settings**:

- **Timeout**: MUST be set to a value tied to the expected run duration plus a safety multiplier. A job that runs in 20 minutes at p99 should have a timeout of 60–90 minutes, not 2880. An over-long timeout is a cost-runaway risk: a stuck job consumes DPU-hours until the timeout fires.
- **MaxRetries**: MUST be set considering job idempotency. For a fully idempotent job (composes with DATAENG-04), 1–2 retries is reasonable. For a non-idempotent job, retries MUST be 0 and orchestration must handle re-invocation through a designed-for-rerun path (composes with ORCH-07). A retry-loop on a non-idempotent job that writes side effects creates duplicates.
- **MaxConcurrentRuns**: MUST be set deliberately. Setting it to 1 prevents concurrent overruns but causes upstream orchestrators to queue jobs. Setting it >1 allows concurrent runs but the job MUST be designed for it (different bookmark scope per run, partitioned output paths, no shared mutable state).
- Job runs invoked from Step Functions MUST check for an already-running execution before retrying (composes with ORCH-07). The design MUST specify whether the orchestrator or Glue itself owns retry semantics — not both.

**Verification**:
- NFR Requirements captures the expected job duration distribution (p50, p99) and the SLO-based timeout target.
- Infrastructure Design specifies `Timeout`, `MaxRetries`, and `MaxConcurrentRuns` per job with rationale.
- Code Generation reviews confirm the values are set in the job definition; a job using defaults is a P0 finding.
- For non-idempotent jobs, the retry path is designed end-to-end across orchestrator and job and documented; ad-hoc retry-on-orchestrator + retry-on-Glue is flagged.

---

## Rule GLUE-08: Continuous Logging, Metrics, and Job Run Insights Enabled

**Severity**: P1 — Warning

**Rule**: Production Glue Spark jobs MUST emit continuous CloudWatch logs, Glue job metrics, and Glue Job Run Insights. Spark UI event logs MUST be persisted to S3 for jobs expected to run more than 5 minutes. The default Glue logging configuration — driver and executor logs only on completion, no metrics, no insights — does not provide enough signal to debug a failed or slow production run; teams that ship with defaults are then surprised when the post-incident review has no usable data.

**Required configuration**:

- `--enable-continuous-cloudwatch-log true` and `--continuous-log-logGroup` set to a project-specific log group with a documented retention. Continuous logging streams driver and executor stdout/stderr in near-real-time, allowing live troubleshooting of running jobs.
- `--enable-metrics true` emits Glue's job-level CloudWatch metrics (DPU usage, executor count, memory pressure) — required for capacity dashboards and Auto Scaling tuning.
- `--enable-job-insights true` produces Glue's automated diagnostic reports for failed runs (skewed partitions, executor OOM, common Spark anti-patterns). For any non-trivial job this saves hours of post-failure analysis.
- `--enable-spark-ui true` and `--spark-event-logs-path` pointed to a project-controlled S3 path. The Spark UI history server is the primary diagnostic for slow stages and skewed shuffles. Event logs MUST land in a path that is not deleted by lifecycle policies sooner than the operational review cadence (e.g., 30 days minimum).
- Log group retention MUST be set explicitly. The Glue-default 14-day retention is too short for weekly on-call rotations and quarterly audits; 90 days is a reasonable baseline.

**CloudWatch dashboards and alerts**:

- Each production job MUST have at least one CloudWatch alarm: job failure (one or more failed runs in a rolling window) or job duration breach (p99 duration exceeds SLO).
- A central dashboard listing DPU-hours per job over the last 30 days MUST exist; cost regressions show up here before they show up on the bill (composes with DATAENG-11).

**Verification**:
- Infrastructure Design specifies log group name, retention, S3 event log path, and the CloudWatch alarms per job.
- Code Generation reviews confirm `--enable-continuous-cloudwatch-log`, `--enable-metrics`, `--enable-job-insights`, and (where applicable) `--enable-spark-ui` are present in every production job's arguments.
- Build and Test confirms the job's log group and event log path are reachable and contain entries after a test run.
- The runbook (composes with RSHIFT-12 pattern of named diagnostic surfaces) lists Glue Job Run Insights and Spark UI as the first stops for slow or failed runs.

---

## Rule GLUE-09: Streaming ETL Discipline — Checkpoints, Watermarks, Schema Registry

**Severity**: P1 — Warning

**Rule**: Glue Streaming ETL jobs MUST set an explicit checkpoint location, configure a micro-batch window appropriate to the source rate, define watermarks for late-arriving data, and bind to a schema registry for any structured streaming source. Streaming jobs left at defaults exhibit silent data loss on operator restarts, unbounded state accumulation, and schema drift that corrupts downstream tables.

**Checkpoint requirements**:

- `checkpointLocation` MUST be set to a project-controlled S3 path scoped per job and per job version. Re-using a checkpoint location across job versions causes Spark to refuse to start with a state-incompatibility error; using a single shared checkpoint across multiple jobs causes silent state corruption.
- The checkpoint location MUST have an S3 lifecycle policy that does NOT expire current checkpoint data. Lifecycle expiration on a checkpoint location is a recurring incident pattern — the job appears healthy until the operator restart, at which point the missing checkpoint forces a restart from earliest offsets and re-emits hours of already-processed data.
- A checkpoint reset procedure MUST be documented (analogous to the bookmark reset procedure in GLUE-03): when intentional state reset is needed, the runbook specifies the steps to drain downstream consumers, clear the checkpoint, and restart with a defined start position.

**Micro-batch window**:

- `windowSize` MUST be set deliberately. The Glue default of 100 seconds is reasonable for many workloads but is not universal. Sub-30-second windows for low-rate sources cause excess Spark scheduling overhead and high empty-batch counts; multi-minute windows on high-rate sources accumulate excess in-memory state and increase end-to-end latency.
- The chosen window MUST be justified in NFR Requirements against the source's measured event rate and the downstream latency SLO.

**Watermarks and late data**:

- Streaming jobs that aggregate or join MUST define an event-time watermark. Without one, state grows unboundedly and the job fails after hours or days with executor OOM.
- The watermark delay MUST be chosen against the measured 99th-percentile late-arrival distribution of the source — not a round number. A 10-minute watermark on a source where 1% of events arrive 30 minutes late silently drops 1% of events.
- The job MUST emit a metric for late-data drop count; a streaming job that drops late data without telemetry has a silent data-loss bug waiting to be discovered downstream (composes with DATAENG-09 backfill).

**Schema registry binding**:

- Streaming ETL jobs reading from Kinesis, MSK, or self-managed Kafka MUST bind to AWS Glue Schema Registry (composes with DATAENG-01). The job arguments specify the registry name, schema name, and compatibility mode. Reading streaming data without a schema registry contract is a P0 finding for any production source feeding a contracted downstream table.
- Schema evolution events on the source registry MUST not deploy automatically to a running streaming job. Streaming jobs MUST be restarted on a controlled schedule when the source schema changes, with the new schema version pinned in the job arguments.

**Verification**:
- Infrastructure Design specifies the checkpoint S3 path per streaming job, lifecycle policy on that path, and the documented checkpoint reset runbook.
- NFR Requirements documents the source event rate, target end-to-end latency, late-arrival distribution, and the chosen `windowSize` and watermark delay with rationale.
- Code Generation reviews confirm `checkpointLocation`, `windowSize`, watermark configuration, schema registry binding, and late-data metric emission are present.
- Build and Test includes a checkpoint-recovery test: kill the job mid-batch and confirm restart resumes from the checkpoint without re-emitting committed records.

---

## Rule GLUE-10: Interactive Sessions and Visual Authoring Governed Separately From Production

**Severity**: P1 — Warning

**Rule**: Glue interactive sessions, Glue Studio (visual authoring), and Glue DataBrew are development and exploration surfaces. They MUST NOT bypass the controls that apply to production jobs: IAM scope, cost controls, and code-in-version-control discipline. The most common pattern of Glue cost runaway and untracked production code traces back to interactive sessions left running and Studio jobs deployed straight from the console.

**Interactive Sessions**:

- Interactive Sessions MUST run under a dedicated dev/exploration IAM role — separate from any production job role. The dev role MUST NOT have write permission to production catalog databases, production S3 prefixes, or production Redshift schemas.
- Every interactive session MUST have an idle timeout (`idle_timeout`) and a maximum session lifetime (`max_capacity`-bound or time-bound) set deliberately. The default — sessions live until manually stopped — has produced multi-week running sessions billing continuously. A default idle timeout of 30 minutes and a max session lifetime of 8 hours is a reasonable starting point.
- Sessions MUST carry cost allocation tags identifying the user and the project (composes with DATAENG-11). Untagged sessions are routinely the largest single line items in unexpected Glue bills.
- Notebook code that becomes production code MUST go through the same Code Generation and review path as any other Glue job. Promoting a notebook directly to a scheduled Glue job by exporting and uploading the script bypasses CI entirely — prohibited (composes with CICD-01, CICD-04).

**Glue Studio (visual authoring)**:

- Glue Studio jobs MUST be exported to script form and committed to version control. Visual-only jobs that exist only in the Glue console are prohibited in production — they cannot be reviewed in PRs, cannot be diffed across versions, and cannot be redeployed reproducibly (composes with CICD-01).
- The Studio-generated script MAY be the canonical artifact (with the visual graph regenerated from the script on console open), or the visual graph MAY be the canonical artifact (with the script regenerated on save) — but the design MUST pick one and document it. Maintaining both as canonical leads to drift.
- Studio jobs deployed from the console without going through the project's CI pipeline are a P0 finding regardless of how trivial the change appears (composes with CICD-06).

**Glue DataBrew**:

- DataBrew recipes MUST be versioned and exported to the project repository. A recipe authored in the DataBrew UI and never exported is identical in governance terms to an unversioned production script.
- DataBrew profile jobs and projects MUST carry the same IAM scoping, cost tagging, and S3 path discipline as Glue ETL jobs. The DataBrew console exposes a different UI, not a different governance regime.

**Verification**:
- Infrastructure Design specifies the dev IAM role for interactive sessions, the idle and max-lifetime defaults, and the cost allocation tagging convention.
- Build and Test (or operational runbook) includes a weekly review process surfacing long-running interactive sessions and untagged sessions for shutdown.
- Code Generation reviews confirm every Glue Studio job in production has a corresponding committed script and that the project's canonical-form choice (script or visual graph) is documented.
- DataBrew recipes used in production are listed in the project repository alongside the Glue ETL job inventory.
