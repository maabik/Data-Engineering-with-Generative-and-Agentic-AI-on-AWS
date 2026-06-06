# S3 Lakehouse Extension

Rules for data engineering workloads using Amazon S3 as the storage layer with open table formats (Apache Iceberg or Delta Lake), AWS Glue Data Catalog as the metastore, and optionally AWS Lake Formation for fine-grained access control.

This extension layers on top of `data-engineering/baseline`. Enable both when building a lakehouse on AWS.

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule S3LH-01: Open Table Format Required for Persisted Tables

**Severity**: P0 — Blocking

**Rule**: Any S3 dataset that is registered as a table, queried by name through a SQL engine, or modified after initial write MUST use an open table format (Apache Iceberg or Delta Lake). Raw Parquet, Avro, or ORC directories registered as Hive tables are not acceptable for new work because they cannot provide ACID guarantees, schema evolution, or safe concurrent writes. Raw object layouts are allowed only for landing-zone ingestion that is immediately promoted into a table-format dataset.

**Verification**:
- Functional Design names the table format (Iceberg or Delta) for every persisted dataset.
- Code Generation produces CREATE TABLE statements using the chosen format and writes via the format's APIs.
- Hive-style append-only directory writes are flagged as findings unless documented as landing-zone only.

---

## Rule S3LH-02: Glue Data Catalog as the Sole Metastore for Production Tables

**Severity**: P0 — Blocking

**Rule**: Production tables MUST be registered in AWS Glue Data Catalog (or a Glue-compatible catalog such as S3 Tables, which is built on it). Engine-local metastores (Spark in-memory metastore, Trino file-based metastore) are not acceptable for production. Cross-engine access (Athena, EMR, Redshift Spectrum, Glue ETL, SageMaker) is a design requirement.

**Verification**:
- Infrastructure Design specifies the catalog (Glue Catalog, S3 Tables, REST catalog backed by Glue).
- Code Generation creates tables through the catalog, not as engine-local externals.
- Build and Test verifies the table is queryable from at least two engines if cross-engine access is in scope.

---

## Rule S3LH-03: Partition Strategy Validated Against File Size Targets

**Severity**: P1 — Warning

**Rule**: Partitioning MUST be designed against a target file size of 128 MB to 1 GB per file post-compaction. A combination of partition column cardinality and ingestion volume that produces files smaller than 64 MB or larger than 2 GB at steady state is a design defect. For Iceberg, hidden partitioning with bucket/truncate transforms is preferred over exposing partition columns to consumers.

**Verification**:
- Functional Design estimates files per partition per day based on ingestion volume.
- File size projection is within target range or a compaction job is specified.
- Iceberg tables use partition transforms (`bucket`, `truncate`, `days`, `hours`) rather than synthetic partition columns where possible.

---

## Rule S3LH-04: Table Maintenance Jobs Required and Scheduled

**Severity**: P1 — Warning

**Rule**: Every Iceberg or Delta table MUST have associated maintenance jobs: compaction (rewrite_data_files / OPTIMIZE), snapshot expiration (expire_snapshots / VACUUM), and orphan file cleanup (remove_orphan_files). Maintenance MUST be scheduled, not manual. Tables left without maintenance accumulate small files, manifest bloat, and S3 storage cost from retained snapshots.

**Verification**:
- Infrastructure Design schedules maintenance for every managed table (frequency justified by write rate).
- Code Generation or IaC includes the maintenance job definitions.
- Retention policy for snapshots is set (default Iceberg 5 days is acceptable; Delta default 7 days is acceptable) and aligned with time-travel requirements in the data contract.

---

## Rule S3LH-05: Concurrent Writers Use Optimistic Concurrency

**Severity**: P0 — Blocking

**Rule**: When more than one writer can target the same table (multiple jobs, batch + streaming, backfill + incremental), the table format's optimistic concurrency mechanism MUST be configured and writers MUST retry on commit conflict. Naive parallel writers without conflict resolution corrupt manifests and cause silent data loss in some engines.

**Verification**:
- Functional Design identifies all writers per table.
- For tables with multiple writers: Iceberg `commit.retry.num-retries` and related properties are set, or Delta `txn.appId` and retry logic is implemented.
- Tests include a concurrent-writer scenario for any table with multiple producers.

---

## Rule S3LH-06: Bronze-Silver-Gold (or Equivalent) Zone Separation

**Severity**: P1 — Warning

**Rule**: The S3 layout MUST separate ingestion zones from curated zones — naming convention is not prescribed (raw/bronze/silver/gold, landing/staging/curated, etc.) but separation is mandatory. Raw data MUST be retained in its source form (or close to it) so transformations can be replayed. Direct writes from a producer into a curated zone are prohibited.

**Verification**:
- Functional Design includes a zone diagram with arrows showing only valid promotions.
- Bucket and prefix naming distinguishes zones.
- IAM and Lake Formation grants align with zones (producers write to landing, transformations write across zones, consumers read from curated).

---

## Rule S3LH-07: SSE-KMS Encryption with Customer-Managed Keys

**Severity**: P0 — Blocking

**Rule**: Production buckets containing data classified as `internal`, `confidential`, `pii`, `phi`, or `pci` (per DATAENG-06) MUST use SSE-KMS with a customer-managed KMS key (CMK), not SSE-S3 or an AWS-managed key. Key policies MUST restrict decrypt access to specific IAM roles. S3 Bucket Keys SHOULD be enabled to control KMS request costs.

**Verification**:
- Infrastructure Design specifies CMK ARN, key policy, and rotation policy per bucket.
- Bucket default encryption is set to SSE-KMS with the CMK.
- S3 Bucket Keys are enabled on buckets with significant write volume (justification documented if disabled).
- Cross-account access patterns explicitly grant kms:Decrypt to the consuming principal.

---

## Rule S3LH-08: Lake Formation for Fine-Grained Access Control on Shared Tables

**Severity**: P1 — Warning

**Rule**: Tables that need column-level or row-level access control, or that are shared across AWS accounts, MUST be managed by AWS Lake Formation. IAM-only access (bucket policy + role permissions) is acceptable only for tables where every reader needs all columns and all rows.

**Verification**:
- Infrastructure Design names tables under Lake Formation governance.
- Column-level filters, row-level filters, and data filters are specified per persona/role for governed tables.
- Cross-account sharing uses LF-Tags or named resource grants, not bucket policies.
- IAMAllowedPrincipals is removed from Lake Formation settings on Glue databases and tables that are governed.

---

## Rule S3LH-09: Parquet as Default Format with Explicit Compression

**Severity**: P2 — Advisory

**Rule**: Parquet is the default columnar format for new tables unless a specific reason exists to choose otherwise. Compression codec MUST be explicitly set (ZSTD recommended for analytical workloads; SNAPPY acceptable for write-throughput-critical workloads). Row group size and page size MUST be tuned for the expected query pattern when defaults are not appropriate.

**Verification**:
- Functional Design names the file format, compression codec, and any non-default row group / page size settings with justification.
- Code Generation writes Parquet with explicit codec configuration.
- ORC or other formats require explicit justification.

---

## Rule S3LH-10: Time Travel Retention Aligned to Recovery and Audit Requirements

**Severity**: P1 — Warning

**Rule**: Snapshot retention (Iceberg) or version retention (Delta) MUST be sized intentionally against two requirements: how far back a consumer needs to query historical state (time travel), and how much restore-from-corruption headroom the operations team needs. The default (5–7 days) is acceptable for most workloads but MUST be a deliberate choice, not an accepted default. Retention beyond 30 days has material storage cost implications.

**Verification**:
- Data contract or NFR Requirements states the time-travel SLA per table.
- Infrastructure Design sets snapshot/version retention properties to match.
- Cost model accounts for retained data file storage at the chosen retention.

---

## Rule S3LH-11: Column Statistics Configured for Query-Critical Tables

**Severity**: P1 — Warning

**Rule**: Every table with a declared query latency SLA (per DATAENG-12) MUST have column statistics explicitly configured and kept current. Query engines use column-level statistics — stored in Iceberg manifest files or the Delta log — to skip data files whose min/max bounds exclude a predicate, reducing bytes scanned without changing query results. Tables where statistics are absent, stale, or misconfigured silently degrade to full scans and will miss their SLA targets under production load.

Statistics configuration MUST be deliberate rather than defaulted:

**Iceberg**:
- `write.metadata.metrics.default` controls the metric mode for all columns. Acceptable values: `truncate(N)` (default; collects truncated bounds — acceptable for most string columns), `full` (exact bounds — preferred for numeric, date, and timestamp filter columns), `counts` (null and NaN counts only — use for columns never used in range predicates), `none` (disables statistics — use only for high-cardinality free-text columns such as `description` or `json_payload` where bounds have no pruning value).
- Per-column overrides via `write.metadata.metrics.column.<col_name>` MUST be used when the default mode is wrong for a specific column (e.g., set `full` on a `event_timestamp` column while leaving string columns at `truncate(16)`).
- Tables wider than 100 columns MUST explicitly set `none` on columns that are never used in filter predicates to prevent manifest bloat.

**Delta Lake**:
- `delta.dataSkippingNumIndexedCols` controls how many leftmost columns are included in per-file statistics written to the Delta log. The default (32) is not always appropriate — it silently skips statistics for filter columns that fall beyond position 32, and wastes log space collecting statistics on columns that are never filtered.
- This property MUST be set to cover at minimum every column that appears in a `WHERE` clause in any SLA-bound query, regardless of column position.
- `ANALYZE TABLE <table> COMPUTE STATISTICS FOR COLUMNS <col1>, <col2>, ...` MUST be run after initial load and after any backfill that materially changes value distributions. For incrementally written tables, statistics refresh MUST be scheduled as part of the maintenance cadence defined in S3LH-04.

**Verification**:
- Functional Design lists, per SLA-bound table: the columns used in WHERE clauses and JOIN predicates of the top queries, the chosen metric mode per column (Iceberg) or the `dataSkippingNumIndexedCols` value and covered columns (Delta), and the statistics refresh cadence.
- Code Generation sets `write.metadata.metrics.*` properties in Iceberg table DDL and sets `delta.dataSkippingNumIndexedCols` in Delta table properties where the default is insufficient.
- Infrastructure Design includes `ANALYZE TABLE` execution in the maintenance schedule (S3LH-04) for Delta tables and for Iceberg tables where `rewrite_manifests` is needed to refresh statistics after bulk rewrites.
- Build and Test executes each SLA-bound query with statistics in place and asserts that the query engine reports skipped files (via `EXPLAIN` output, query profile, or engine scan metrics) — a full scan on a filtered table with statistics configured is a test failure.

---

## Rule S3LH-12: Iceberg Branches and Tags Used for CI/CD Isolation and Audit Snapshots

**Severity**: P1 — Warning

**Rule**: Projects using Apache Iceberg MUST use Iceberg's branch and tag primitives to isolate in-flight changes from production readers and to preserve point-in-time audit snapshots. Writing speculatively to the `main` branch while CI validation is in progress exposes partially-validated data to downstream consumers and is prohibited for any table with a declared SLA or downstream consumers listed in the data contract.

**Branch workflow**:
- Every pull request that modifies transformation logic, schema, or seed data for an Iceberg table MUST write to a named Iceberg branch scoped to that PR (e.g., `pr-<number>` or `ci-<commit-sha>`), not to the `main` branch.
- CI validation — data quality checks (DATAENG-07), schema contract enforcement (DATAENG-01), and statistics verification (S3LH-11) — runs against the PR branch. Only a clean CI run unblocks promotion.
- On PR merge, the PR branch is fast-forward merged into `main` via `ALTER TABLE ... SET CURRENT SNAPSHOT` or the engine's branch merge API. The PR branch is deleted after successful promotion.
- The `main` branch is the only branch read by downstream production consumers. Production queries MUST NOT reference PR or CI branches.

**Tag workflow**:
- A named Iceberg tag MUST be created at every production deployment boundary (e.g., `deploy-<YYYY-MM-DD>-<commit-sha>`) immediately before any schema migration or bulk write is applied to `main`. Tags are immutable snapshot references — they preserve a restore point without retaining live data files beyond the configured retention.
- Tags used for regulatory audit snapshots MUST have an explicit `max-ref-age-ms` set to match the audit retention requirement (e.g., 7 years for financial data) and MUST NOT rely on the table's default snapshot expiration policy, which would silently delete them.
- Tags MUST NOT be used as write targets; they are read-only snapshot pointers.

**Branch and tag retention**:
- Merged PR branches MUST be deleted within one maintenance cycle (S3LH-04) to prevent manifest accumulation.
- Long-lived audit tags are exempt from snapshot expiration — `expire_snapshots` procedures MUST be configured to retain snapshots referenced by named tags. Iceberg's `expire_snapshots` does not expire snapshots pinned by a tag by default; this behaviour MUST be verified in Build and Test.
- The number of concurrent live PR branches on a single table MUST be bounded (recommended maximum: 10) to prevent metadata file growth; the CI/CD system MUST enforce this limit by deleting stale branches from abandoned PRs.

**Verification**:
- Infrastructure Design documents the branch naming convention, the promotion procedure (merge API call or `SET CURRENT SNAPSHOT`), and the tag naming convention with retention overrides for audit tags.
- Code Generation configures the pipeline's write target as the PR branch reference, not `main`, for CI runs; promotion to `main` is a separate explicit step in the CI/CD pipeline (per CICD-06).
- Build and Test includes: (a) a CI run that writes to a PR branch and asserts `main` is unchanged; (b) a promotion step that merges the PR branch and asserts `main` reflects the new snapshot; (c) a verification that `expire_snapshots` does not remove a snapshot pinned by a named tag.
- Brownfield projects that currently write directly to `main` without branch isolation document a migration plan to adopt the branch workflow; direct-to-main writes are accepted as a P1 finding with a remediation timeline, not silently grandfathered.

---

## Rule S3LH-13: S3 Lifecycle Policies for Cold Partition Cost Management

**Severity**: P1 — Warning

**Rule**: Every S3 bucket used as a lakehouse storage layer MUST have explicit S3 lifecycle policies that transition or expire objects based on age and access pattern. Storing all partitions indefinitely in S3 Standard is a cost defect for any table whose historical partitions are accessed infrequently. Lifecycle configuration MUST be a deliberate design decision per bucket and per partition age tier — not an omission.

**Storage class selection**:

| Use case | Recommended class | Rationale |
|----------|-------------------|-----------|
| Active partitions (last 30–90 days), queried frequently | S3 Standard | No retrieval cost; lowest latency |
| Warm partitions (90 days–1 year), queried occasionally | S3 Intelligent-Tiering | Automatic movement between Frequent and Infrequent Access tiers based on observed access patterns; no retrieval fee for objects that move back to Frequent Access |
| Cold partitions (1–3 years), queried rarely but must remain queryable | S3 Intelligent-Tiering (Archive Instant Access tier enabled) | Sub-millisecond retrieval like Standard-IA but lower storage cost; no minimum retrieval fee |
| Archive partitions (>3 years), queried only for compliance or audits | S3 Glacier Instant Retrieval or S3 Glacier Flexible Retrieval | Lowest storage cost; retrieval fee and latency are acceptable when access frequency is near-zero |
| Objects past retention requirement | Expire and delete | Retaining objects beyond the retention requirement defined in the data contract (DATAENG-01) and time-travel policy (S3LH-10) is a compliance and cost liability |

**S3 Intelligent-Tiering specifics**:
- The monitoring and automation fee (per-object charge) makes Intelligent-Tiering cost-ineffective for objects smaller than 128 KB. Lifecycle rules MUST NOT apply Intelligent-Tiering to small-file objects; this is a direct consequence of the file size discipline in S3LH-03 — properly compacted tables naturally satisfy the 128 KB floor.
- The Archive Access and Deep Archive Access tiers within Intelligent-Tiering introduce retrieval latency (hours) and MUST only be enabled on buckets where that latency is acceptable to all consumers. When enabled, the activation thresholds (90 days for Archive Access, 180 days for Deep Archive Access) MUST be documented alongside the consumer SLA to confirm compatibility.
- Intelligent-Tiering does not suit landing-zone buckets with high object churn (objects written and deleted within days) — Standard with a short expiration rule is cheaper there.

**Iceberg and Delta interaction**:
- Data files (Parquet) are candidates for tiering based on partition age. Metadata files (Iceberg manifests, manifest lists, `version-hint.text`; Delta `_delta_log/` JSON and checkpoint files) MUST remain in S3 Standard or S3 Standard-IA — transitioning metadata files to Glacier breaks table reads without warning.
- Lifecycle rules MUST use prefix or tag conditions to exclude metadata paths from tiering transitions. For Iceberg: exclude `<table-prefix>/metadata/`. For Delta: exclude `<table-prefix>/_delta_log/`.
- Orphan data files cleaned by `remove_orphan_files` (S3LH-04) are deleted explicitly by the maintenance job; lifecycle expiration is a safety net for files the maintenance job misses, not the primary deletion mechanism.

**Verification**:
- Infrastructure Design includes a lifecycle policy matrix per bucket: prefix or tag scope, age threshold per transition tier, storage class target, and a note on whether Intelligent-Tiering archive tiers are enabled and why.
- Metadata prefixes are explicitly excluded from tiering transitions in the lifecycle rule configuration.
- The lifecycle age thresholds are reconciled against the time-travel retention from S3LH-10 — a lifecycle rule that expires data files before the snapshot retention window closes is a blocking finding.
- Infrastructure as Code (CDK, Terraform, CloudFormation) provisions lifecycle rules alongside bucket creation; lifecycle configuration is not applied manually after deployment.
- Build and Test documents a cost model: estimated monthly S3 cost before and after lifecycle policy at projected steady-state data volume, demonstrating the policy is sized to the actual access pattern rather than copied from a template.
