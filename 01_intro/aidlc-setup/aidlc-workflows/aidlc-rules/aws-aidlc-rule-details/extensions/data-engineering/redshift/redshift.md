# Amazon Redshift Extension

Rules for data engineering workloads using Amazon Redshift as a warehouse — both provisioned (RA3) and Serverless. Covers Redshift native tables, materialized views, Spectrum external tables, and data sharing.

This extension layers on top of `data-engineering/baseline`.

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule RSHIFT-01: Distribution Style Chosen and Documented Per Table

**Severity**: P1 — Warning

**Rule**: Every Redshift native table MUST have an explicitly chosen distribution style (KEY, ALL, EVEN, or AUTO) with documented rationale. Defaulting to AUTO is acceptable only for tables under 10 GB or where workload patterns are not yet known; tables above 10 GB with stable workload MUST have a deliberate choice. KEY distribution on the join column is required for fact-to-dimension joins that scan large volumes.

**Verification**:
- Functional Design includes a table-level matrix: table name, expected size, primary join pattern, chosen distribution style, rationale.
- DDL in Code Generation matches the design.
- KEY distribution is chosen on join columns for declared fact-dimension relationships.
- ALL is reserved for dimension tables under approximately 3 million rows or 2–3 GB.

---

## Rule RSHIFT-02: Sort Key Strategy Justified Against Query Predicates

**Severity**: P1 — Warning

**Rule**: Every table MUST have a sort key chosen against the actual filter predicates in the most expensive queries. Compound sort keys are the default; interleaved sort keys are used only when multiple equally weighted filter columns are queried and the cost of VACUUM REINDEX is acceptable. Sort key columns MUST be present in WHERE clauses of the workload's top-10 queries by cost.

**Verification**:
- Functional Design lists sort key columns per table with example WHERE predicates that benefit.
- For interleaved sort keys: VACUUM REINDEX schedule is in Infrastructure Design.
- Tables without a sort key are flagged as findings unless explicitly documented as low-volume or fully-scanned tables.

---

## Rule RSHIFT-03: Workload Concurrency and Queue Management Designed for Deployment Type

**Severity**: P1 — Warning

**Rule**: Workload management configuration MUST be explicitly designed for the chosen deployment type. The mechanisms differ fundamentally between provisioned and Serverless, and applying provisioned WLM concepts to Serverless (or ignoring Serverless-specific controls) is a design defect.

**Redshift Provisioned (RA3)**:
- WLM MUST be configured for the workload mix. Automatic WLM with query priorities is the default and covers most cases; manual WLM queues are used only when hard queue isolation (separate memory, concurrency slots, timeout) is required for a specific workload class.
- Concurrency Scaling MUST be enabled for clusters with read-heavy or burst workloads unless a cost analysis explicitly excludes it; usage limits MUST be set per queue to bound Concurrency Scaling credit consumption.
- Short Query Acceleration (SQA) MUST be evaluated; it reduces latency for short queries without requiring a separate manual WLM queue and should be preferred over a dedicated short-query queue for most provisioned configurations.
- Query monitoring rules (QMR) MUST be defined for the superuser queue to detect and abort runaway queries that would otherwise starve other queues.

**Redshift Serverless**:
- Redshift Serverless has no WLM — queue management is automatic and not user-configurable. Rules about WLM queues, queue slots, and queue memory allocation do not apply to Serverless deployments and MUST be marked N/A.
- The primary concurrency and throughput lever for Serverless is the RPU (Redshift Processing Unit) range: `base RPU` sets the minimum compute available at all times and `max RPU` caps peak autoscaling. Both MUST be set with a documented basis — leaving `max RPU` at the account default is not an acceptable substitute for a sizing decision.
- Usage limits MUST be configured on the Serverless namespace to cap credit consumption and prevent cost runaway (composes with DATAENG-11). At minimum: a daily or weekly RPU-hour limit with a `LOG` action and a hard `STOP` action at a higher threshold.
- Query priority within Serverless is controlled by query groups and associated priorities (`CRITICAL`, `HIGH`, `NORMAL`, `LOW`, `LOWEST`) set via `SET query_group`. Workloads with differentiated SLAs MUST use query groups to signal priority to the Serverless scheduler; an undifferentiated workload where all queries compete equally is acceptable only when all consumers have identical SLA requirements.

**Verification**:
- NFR Requirements identifies the workload mix (ETL writes, BI reads, ad-hoc queries), their priorities, and the deployment type (provisioned or Serverless). Rules inapplicable to the chosen deployment type are marked N/A in the compliance summary.
- **Provisioned**: Infrastructure Design specifies WLM mode (automatic or manual), queue definitions with memory and concurrency allocations, SQA enabled/disabled with rationale, Concurrency Scaling usage limits per queue, and at least one QMR rule on the superuser queue.
- **Serverless**: Infrastructure Design specifies base RPU, max RPU with sizing rationale (peak query concurrency, expected query duration, and data volume scanned per query), namespace usage limits with both LOG and STOP thresholds, and query group priority assignments for differentiated workloads.
- Build and Test benchmarks representative queries from each workload class under concurrent load and asserts latency targets are met without one workload class starving another.

---

## Rule RSHIFT-04: Materialized Views Use Incremental Refresh Where Possible

**Severity**: P2 — Advisory

**Rule**: Materialized views MUST be evaluated for incremental refresh eligibility (Redshift documents specific SQL constructs supported). MVs that fall back to full recompute are acceptable only for small base tables or low refresh frequency. Auto Refresh is preferred over scheduled refresh when query patterns benefit.

**Verification**:
- Functional Design notes each MV as incremental-eligible or full-recompute, with refresh strategy.
- Code Generation creates MVs with AUTO REFRESH where appropriate.
- Disqualifying SQL constructs (outer joins to non-PK columns, certain window functions, etc.) are avoided in incremental-eligible MVs or the MV is reclassified.

---

## Rule RSHIFT-05: Spectrum vs Native Decision Documented Per Dataset

**Severity**: P1 — Warning

**Rule**: For every dataset accessed through Redshift, the design MUST state whether it lives as a native Redshift table or as a Spectrum external table on S3, with rationale. Spectrum is preferred for datasets that are infrequently joined to native tables, are owned by a separate pipeline that already lands them in S3, or exceed cost-effective native storage. Joining a large Spectrum table to a large native table without distribution key alignment is a performance anti-pattern.

**Verification**:
- Functional Design includes a per-dataset native-vs-Spectrum matrix with rationale.
- For Spectrum tables: external schema and table definitions reference Glue Catalog (composes with S3LH-02 if `s3-lakehouse` is enabled).
- Spectrum queries scan partitioned data; full table scans across partitions are flagged unless justified.

---

## Rule RSHIFT-06: VACUUM and ANALYZE Cadence Defined

**Severity**: P1 — Warning

**Rule**: Tables with significant update or delete activity MUST have a VACUUM cadence (Auto Vacuum is acceptable for most patterns; explicit VACUUM SORT/DELETE is used when Auto Vacuum cannot keep up). Statistics MUST be refreshed via ANALYZE after material data changes; AUTO ANALYZE is acceptable when staleness is bounded. The design MUST acknowledge each Auto setting as a deliberate choice.

**Verification**:
- Infrastructure Design lists tables with material update/delete activity and their maintenance approach.
- For high-velocity tables, explicit maintenance scheduling is in place rather than relying on Auto.
- ANALYZE runs after large loads (post-COPY ANALYZE pattern).

---

## Rule RSHIFT-07: Column Compression Encodings Set Explicitly on Wide Tables

**Severity**: P2 — Advisory

**Rule**: Tables with more than 20 columns or expected size over 50 GB MUST have column compression encodings explicitly set. Leaving encodings to Redshift's automatic selection (RAW default on `CREATE TABLE`, or `COMPUPDATE ON` during COPY without review) is prohibited for tables of this size because the automatic choice is made per-batch and may not reflect the full value distribution.

**ANALYZE COMPRESSION sampling**:
- `ANALYZE COMPRESSION` MUST be run against a representative data sample before DDL is finalised. "Representative" means at minimum 10–20% of expected steady-state row count, covering the same value distributions (cardinality, skew, seasonal patterns) that production data will have. Running `ANALYZE COMPRESSION` on an empty table or a trivially small seed dataset produces misleading encoding recommendations and is prohibited.
- The output of `ANALYZE COMPRESSION` MUST be captured as a design artifact (screenshot, table, or script output) and retained alongside the DDL. This creates an audit trail for future re-encoding decisions and prevents the sample step from being silently skipped.
- Recommendations from `ANALYZE COMPRESSION` are a starting point, not an instruction to accept blindly. The design MUST apply the following overrides where the tool's recommendation conflicts with known best practice:
  - AZ64 is preferred over LZO for numeric (`INT`, `BIGINT`, `DECIMAL`) and date/time columns — AZ64 compresses and decompresses faster on modern hardware.
  - ZSTD is preferred over LZO or BYTEDICT for variable-length strings with low-to-moderate cardinality — better compression ratio with acceptable CPU cost.
  - RAW is appropriate for single-byte boolean-equivalent columns and very-high-cardinality columns (e.g., UUID surrogate keys) where any encoding adds CPU overhead without compressing effectively.
  - DELTA or DELTA32K is appropriate for monotonically increasing numeric sequences (e.g., auto-increment IDs, unix epoch timestamps) — `ANALYZE COMPRESSION` often misses this because it inspects value distributions, not sequence patterns.

**Re-encoding and the VACUUM FULL interaction**:
- Changing a column's compression encoding on an existing populated table **requires a full table rewrite** — there is no in-place re-encoding operation in Redshift. The options are: (a) `CREATE TABLE new AS SELECT ... FROM old` with the corrected DDL followed by a rename, (b) `ALTER TABLE ... ALTER COLUMN ... ENCODE` (available in newer Redshift versions) which internally triggers the same full rewrite, or (c) `VACUUM FULL` — which does NOT re-encode columns. `VACUUM FULL` reorganises physical storage and sorts data but preserves the existing encoding on each block; it cannot change encodings.
- This cost MUST be acknowledged in the design before DDL is committed to production. A table that requires re-encoding after initial load imposes a maintenance window proportional to table size and cannot be done transparently under load.
- For tables where encodings are initially uncertain (e.g., a new table whose production value distributions are not yet known), the design MUST document this explicitly and schedule a post-launch `ANALYZE COMPRESSION` run on a representative production sample followed by a planned re-encoding window. Deferring indefinitely is not acceptable.
- `COPY` with `COMPUPDATE PRESET` is acceptable as an initial loading strategy when the encoding choice is deferred, but the DDL produced by PRESET MUST be reviewed and locked before the table exceeds 10 GB — beyond that point, re-encoding carries a meaningful operational cost.

**Verification**:
- Functional Design includes the encoding strategy per column for in-scope tables: column name, chosen encoding, and rationale referencing the `ANALYZE COMPRESSION` output or the override reason above.
- The `ANALYZE COMPRESSION` sample artifact is attached to or referenced from the design document.
- DDL specifies `ENCODE <encoding>` per column; no in-scope table relies on implicit defaults or unreviewed COMPUPDATE output.
- Infrastructure Design acknowledges the re-encoding cost for any table where encodings are deferred post-launch, including the planned re-encoding window and the trigger condition (e.g., "after 30 days of production load, re-run ANALYZE COMPRESSION and schedule re-encoding if recommendations differ from initial DDL").
- LISTAGG and other wide-row-producing patterns are flagged for review — wide intermediate rows can exceed the 4 MB per-row limit and are not mitigated by column encoding alone.

---

## Rule RSHIFT-08: Data Sharing and Federated Query Security Model Documented

**Severity**: P0 — Blocking

**Rule**: When using Redshift Data Sharing (across clusters, across accounts) or Federated Queries (to RDS PostgreSQL, Aurora, etc.), the design MUST specify: which datashares exist, who consumes them, IAM/role bindings, and column-level visibility. Federated queries MUST use Secrets Manager-backed credentials.

**Verification**:
- Infrastructure Design lists datashares: name, producer cluster, consumer accounts/namespaces, included schemas/tables.
- Federated query secrets resolve through Secrets Manager (composes with DATAENG-15).
- Cross-region or cross-account shares have explicit network and IAM justification.

---

## Rule RSHIFT-09: Cluster Sizing — Provisioned vs Serverless Decision Documented

**Severity**: P1 — Warning

**Rule**: The choice between provisioned RA3 and Redshift Serverless MUST be a documented decision based on workload predictability, concurrency profile, and cost analysis. Serverless is preferred for unpredictable or intermittent workloads; provisioned RA3 with reserved instances is preferred for steady, high-utilization workloads. Mixing should be deliberate (e.g., Serverless dev/test + Provisioned prod).

**Verification**:
- NFR Requirements captures workload predictability and concurrency targets.
- Infrastructure Design names the cluster type, node type or base RPU, and reservation strategy.
- Cost projection covers both peak and steady-state.

---

## Rule RSHIFT-10: Result Caching Is a Bonus, Not a Performance Strategy

**Severity**: P1 — Warning

**Rule**: Result caching MUST NOT be relied on for SLA-meeting query performance. Every query that has a stated latency SLA MUST be benchmarked with caching disabled (`SET enable_result_cache_for_session = OFF`). Caching is a real benefit but disappears on the first run after DDL changes, statistics refresh, or cache eviction.

**Verification**:
- NFR Requirements lists queries with latency SLAs.
- Build and Test benchmarks each SLA-bound query with result cache disabled and asserts the SLA holds.
- Performance test instructions include the cache-disabled run as the primary metric.

---

## Rule RSHIFT-11: Redshift Data API vs JDBC/ODBC Connection Method Documented

**Severity**: P1 — Warning

**Rule**: Pick a connection method per caller type and document the choice. JDBC/ODBC as the default for everything is wrong — Lambda functions and other short-lived callers can't hold persistent connections, so each invocation opens and closes a new one. Under concurrent load that exhausts the cluster's connection limit fast.

**Decision criteria**:

| Caller type | Method | Why |
|-------------|--------|-----|
| Lambda, short-lived ECS task, Step Functions Task state | **Data API** | Can't hold a persistent connection; JDBC here causes connection storms |
| AppSync, API Gateway + Lambda backend | **Data API** | Same reason; async execution model fits the Data API naturally |
| Long-running app server, pooled container | **JDBC/ODBC + pool** | Persistent connections amortise auth overhead; Data API latency and per-call cost add up here |
| BI tools (QuickSight, Tableau, Looker) | **JDBC/ODBC** | Most don't support the Data API; they manage their own pools |
| Glue ETL, EMR, Spark | **JDBC** | Distributed engines handle their own parallelism; the Data API's sequential model doesn't fit |
| Ad-hoc scripts, one-off migrations | Either | Data API if no driver available; JDBC otherwise |

**Using the Data API**:

`execute-statement` is asynchronous — it hands back an `Id` immediately, not results. The caller polls `describe-statement` until status is `FINISHED` or `FAILED`, then calls `get-statement-result`. Skip the poll loop and you get nothing back, with no error to show for it.

A few other sharp edges worth knowing:

- **24-hour execution limit.** Anything expected to run longer needs JDBC instead; the Data API will not accept it.
- **Results are paginated at 100 rows.** Use `NextToken` on `get-statement-result` or silently truncate. This is a data correctness bug, not a performance issue.
- **Polling is billed.** A tight polling loop burns API calls and will get throttled. Use exponential backoff.
- **Auth is IAM only.** No database username/password. The calling role needs `redshift-data:ExecuteStatement`, `redshift-data:DescribeStatement`, and `redshift-data:GetStatementResult` scoped to the specific cluster or Serverless workgroup ARN — not `redshift-data:*` on `*` (composes with DATAENG-15 and RSHIFT-08).

**Using JDBC/ODBC**:

- Credentials come from Secrets Manager or IAM — no hardcoded passwords (composes with DATAENG-15).
- On Serverless, IAM-based JDBC auth requires a database user created from the IAM role. Document how that user is provisioned.
- Pool size must be set against the cluster's `max_connections` (provisioned) or the Serverless connection limit. Multiple teams each sizing pools to their own peak will collectively exceed the limit and block each other.

**Verification**:
- NFR Requirements lists every caller type (Lambda functions, application servers, BI tools, ETL jobs) with its assigned connection method.
- Infrastructure Design covers IAM permissions for Data API callers and pool sizing for JDBC/ODBC callers.
- Code Generation implements the async poll-and-paginate loop for every Data API caller — no caller assumes one `get-statement-result` call is enough.
- Build and Test includes a Data API test that fetches a multi-page result set and asserts all pages are retrieved.
- Any Lambda or Step Functions Task state using JDBC/ODBC has a written justification explaining why a persistent connection works in that context.

---

## Rule RSHIFT-12: STL/SVL System Tables Used as the Standard Debugging Toolkit

**Severity**: P2 — Advisory

**Rule**: Teams running production Redshift workloads MUST know which system tables answer which operational questions. Redshift's STL/SVL/STV/SVV views are the primary diagnostic surface — not the console, not CloudWatch alone. Incidents that take hours to diagnose because no one knew which table to query are an operational maturity failure, not a Redshift limitation.

The following views are the minimum set every engineer touching production should be able to use without looking them up:

**Query performance**

- `SVL_QUERY_SUMMARY` — per-step execution breakdown for a completed query: rows processed, bytes, execution time, and whether a step was skipped. Start here when a query is slower than expected. Filter on `query` (query ID) and sort by `maxtime` to find the expensive steps.
- `SVL_QLOG` — lightweight log of recent queries with elapsed time, queue time, and user. Useful for a quick scan of what ran recently and how long it took before pulling the full plan.
- `STL_QUERY` — full query text and timing. Join to `SVL_QUERY_SUMMARY` on `query` when you need the SQL alongside the execution breakdown.

**Alerts and bad query patterns**

- `STL_ALERT_EVENT_LOG` — Redshift's own assessment of query problems: missing statistics, nested loops, hash loops, very selective filters that should be sort-key-aligned, and distribution mismatches causing cross-node data movement. This is the fastest way to find structural problems. A query with multiple alert events is a design finding, not just a slow query.
- `SVL_QUERY_REPORT` — per-slice, per-step execution detail. Use this when `SVL_QUERY_SUMMARY` shows an expensive step and you need to know whether the work is balanced across slices or skewed to one.

**Locks and concurrency**

- `STV_LOCKS` — currently held table locks. When a query hangs, check here first before assuming it's a performance issue. A `DROP TABLE` or `ALTER TABLE` waiting on a lock from a long-running transaction is a common cause of apparent hangs.
- `STL_TR_CONFLICT` — lock conflict history. Helps identify which queries are repeatedly blocking each other so the scheduling or transaction design can be fixed.

**WLM and queuing (provisioned only)**

- `STV_WLM_QUERY_STATE` — live view of queries currently in WLM queues: which queue, what state (queuing, running), and how long they've been waiting. The first place to check when SLA-bound queries are slow due to queue saturation rather than execution time.
- `STL_WLM_QUERY` — completed WLM records with queue wait time vs execution time. If a query consistently spends more time queuing than executing, the WLM queue configuration or concurrency limit needs revisiting.

**Data loads**

- `STL_LOAD_ERRORS` — COPY command errors with file name, line number, and error message. Required first stop after a failed or partial COPY before checking S3 logs.
- `STL_LOAD_COMMITS` — successful COPY completions with row counts. Cross-reference against expected volume per DATAENG-07 after any bulk load.

**Usage on Serverless**: `STL_*` and `STV_*` views are not available on Redshift Serverless — they are provisioned-cluster-only. On Serverless, equivalent diagnostics come from the `SYS_*` views (`SYS_QUERY_HISTORY`, `SYS_QUERY_DETAIL`, `SYS_LOAD_ERROR_DETAIL`, `SYS_ALERT_EVENT_LOG`). Runbooks targeting provisioned system tables will silently return no rows on Serverless; teams MUST maintain separate runbook paths per deployment type.

**Verification**:
- Build and Test documentation includes a runbook section listing the system table queries used to diagnose the three most common failure modes for this pipeline: slow query, failed load, and lock contention.
- The runbook distinguishes provisioned (`STL_*`/`SVL_*`) from Serverless (`SYS_*`) paths where the pipeline may run on either.
- Infrastructure Design enables `pg_stat_statements` or equivalent query history retention settings so system table data survives long enough to be useful during post-incident review (Redshift retains STL data for approximately 2–5 days by default; workloads with weekly on-call rotations should confirm this is sufficient or supplement with CloudWatch Logs export).

---

## Rule RSHIFT-13: Redshift ML Model Governance (Conditional — ML in Scope Only)

**Severity**: P1 — Warning

**Applicability**: This rule applies only when `CREATE MODEL` is used in the project. Mark as N/A otherwise.

**Rule**: Redshift ML (`CREATE MODEL`) trains a SageMaker Autopilot model from a SQL query and exposes it as a SQL function. It looks like a database operation but it isn't — it launches SageMaker training jobs, writes data to S3, and incurs SageMaker compute costs independently of Redshift costs. Treating it as just another SQL statement leads to ungoverned training runs, untracked model versions, PII leaking into training data, and surprise bills.

**Training data**

- The SELECT query passed to `CREATE MODEL` defines the training dataset. Any column included in that query is exported to S3 in plaintext for SageMaker to read. Columns classified as `pii`, `phi`, or `pci` (per DATAENG-06) MUST NOT appear in the training query unless the model's purpose explicitly requires them and a privacy review has been completed and captured in the requirements artifact.
- The S3 bucket Redshift uses for training data export MUST be the project-controlled bucket specified in the `MODEL_PARAMETERS` clause — not the Redshift-managed default. Using the default means training data lands in an AWS-managed location outside the project's KMS and access control boundary (composes with S3LH-07).
- Training data selection must be reproducible. The query MUST reference a versioned snapshot or a time-bounded filter so the same training run can be replicated. Open-ended queries against live tables produce a different model every time they run.

**IAM and cost**

- `CREATE MODEL` requires the Redshift cluster's IAM role to have `sagemaker:CreateAutoMLJob`, `s3:PutObject` on the training bucket, and `iam:PassRole` to a SageMaker execution role. These permissions MUST be scoped to specific resource ARNs — not wildcards — and documented in Infrastructure Design (composes with DATAENG-15 and RSHIFT-08).
- SageMaker Autopilot training costs are separate from Redshift costs and are not covered by Redshift resource monitors (RSHIFT-04 / DATAENG-11). A dedicated SageMaker budget or AWS Budgets alert MUST be configured before any `CREATE MODEL` runs in production. An accidental retraining run on a large dataset can cost hundreds of dollars and will not be caught by Redshift WLM limits.
- `MAX_CELLS` and `MAX_RUNTIME` parameters MUST be set on every `CREATE MODEL` call to bound training time and dataset size. Omitting them allows Autopilot to run to its own defaults, which can be hours and millions of rows.

**Model versioning and promotion**

- Each `CREATE MODEL` call produces a new model version. Model versions MUST follow the same promotion process as pipeline code (composes with CICD-06): train in dev/staging, evaluate against a held-out test set, promote to production only after explicit approval.
- The model function name in production MUST be versioned or use a stable alias (`predict_churn_v2`, not `predict_churn`) so callers are never silently upgraded to a new model without a deployment event. Replacing a model function in-place without a version bump is prohibited.
- Model evaluation metrics (accuracy, F1, AUC, or domain-appropriate equivalents) MUST be captured as a design artifact at promotion time. Deploying a model without recorded evaluation metrics makes it impossible to detect degradation later.

**Model drift and refresh**

- Production models MUST have a defined refresh cadence or a drift detection trigger — the appropriate choice depends on how fast the underlying data distribution changes. A model trained once and never refreshed is a reliability risk; Redshift ML does not monitor drift automatically.
- When a model is retrained, the previous version MUST be retained (not dropped) for at least one SLA cycle so callers can roll back without a full retrain if the new model behaves unexpectedly in production.

**Verification**:
- Requirements Analysis flags ML-in-scope and captures the privacy review outcome for any training query touching classified columns.
- Infrastructure Design specifies the training S3 bucket ARN, KMS key, SageMaker execution role, IAM permissions scoped to specific ARNs, SageMaker budget alert, and the `MAX_CELLS` / `MAX_RUNTIME` values used for each model.
- Code Generation sets `MAX_CELLS`, `MAX_RUNTIME`, and the explicit S3 bucket in every `CREATE MODEL` call; no call omits these parameters.
- Build and Test includes: model evaluation metrics captured against a held-out test set; confirmation that the training query is reproducible against a fixed snapshot; and a check that no classified columns appear in the training query without a recorded privacy review.
- The model promotion record in audit.md includes: model function name and version, training data snapshot identifier, evaluation metrics, approver, and deployment timestamp.
