# Dimensions — full rubric and detection logic

Each dimension is scored 1-5. The level set by `dla scan` is the highest level for which the detection logic returns true.

The scoring rules live in `src/dla/dimensions/<name>.py` — the prose below mirrors what those modules implement. Detection signals come from the snapshot returned by `src/dla/collectors/`.

---

## 1. Storage & Table Format

**Source module**: `src/dla/dimensions/storage_format.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 — Ad hoc | Raw files, no partitioning, no Glue registration | Default if no signal below |
| L2 — Partitioned | Parquet/ORC, Hive partitioning, registered in Glue | `(iceberg+delta+hudi+parquet)/total ≥ 0.5` OR ≥50% Parquet objects in lake buckets OR any partitioned Glue table |
| L3 — Open table format | Iceberg / Delta / Hudi tables | ≥10% open-format share OR Iceberg metadata.json files OR Delta `_delta_log/` prefixes |
| L4 — Managed maintenance | S3 Tables OR scheduled maintenance | S3 Tables in use OR ≥5 Iceberg tables AND ≥1 scheduled Glue trigger |
| L5 — Workload-optimized | Sort/cluster keys, MVs, query-pattern tuning | L4 + Athena MV CREATE/REFRESH events in query history |

---

## 2. Catalog & Schema Management

**Source module**: `src/dla/dimensions/catalog_schema.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | No catalog OR <20% coverage | Default |
| L2 | Crawler-populated Glue catalog | `glue.table_count > 0 AND crawler_count > 0` |
| L3 | ≥80% column descriptions | `described_tables / table_count ≥ 0.5` |
| L4 | DataZone-published assets | DataZone domain active AND ≥1 asset |
| L5 | Mature DataZone, contracts, automated descriptions | DataZone domain active AND ≥50 assets |

---

## 3. Access Control & Security

**Source module**: `src/dla/dimensions/access_security.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | Bucket-level IAM only, missing/SSE-S3 encryption | Default |
| L2 | Most buckets default-encrypted | ≥50% of inspected buckets have any SSEAlgorithm |
| L3 | SSE-KMS with CMKs + LF grants, IAM passthrough off | ≥50% KMS-encrypted AND CMK count > 0 AND LF permissions > 0 AND `iam_passthrough_default = false` |
| L4 | LF-Tag-Based Access Control | L3 + ≥1 LF-Tag and ≥1 LF-Tag-policy permission |
| L5 | Column filters + strict PAB | L4 + ≥1 column-level permission AND ≥80% strict-PAB |

---

## 4. Data Quality & Observability

**Source module**: `src/dla/dimensions/quality_observability.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | No automated checks | Default if no Glue jobs |
| L2 | Glue jobs exist, ad-hoc validation | `job_count > 0` |
| L3 | Deequ/GE in ≥50% of jobs | `(deequ + ge) / job_count ≥ 0.5` (signal: lib name in job name/args) |
| L4 | Freshness SLOs + CloudWatch alarms | Not yet detected — questionnaire in v0.2 |
| L5 | Agent-driven RCA + auto-remediation | Not yet detected — questionnaire in v0.2 |

---

## 5. Performance & Ingestion

**Source module**: `src/dla/dimensions/performance_ingestion.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | Manual loads, no partitioning | Default |
| L2 | Scheduled batch + partitioned tables | ≥1 scheduled trigger AND ≥1 partitioned table |
| L3 | CDC/incremental + filtering queries | L2 + ≥50% of sampled Athena queries have filter clauses |
| L4 | Streaming to Iceberg/S3 Tables | L3 + S3 Tables present OR ≥3 scheduled triggers |
| L5 | Materialized views + result reuse | L4 + Athena MV signals |

---

## 6. Cost & Lifecycle Governance

**Source module**: `src/dla/dimensions/cost_lifecycle.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | No tagging, no lifecycle | Default |
| L2 | Some tags or lifecycle | ≥30% of buckets tagged OR with lifecycle rules OR Intelligent-Tiering transitions |
| L3 | Cost allocation tags + Glacier-IR | DataProduct/Owner cost tag active AND (Glacier transition OR ≥50% lifecycle coverage) |
| L4 | Lifecycle on most buckets | L3 + ≥70% of buckets with lifecycle |
| L5 | Cost anomaly + chargeback | L4 + ≥1 CE anomaly subscription + ≥3 active cost tags |

---

## 7. Lineage & Provenance

**Source module**: `src/dla/dimensions/lineage.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | No lineage tracking | Default |
| L3 | Glue lineage / OpenLineage signal | Glue Crawler `LineageConfiguration=ENABLE` OR OpenLineage signature in jobs |
| L4 | Column-level lineage in DataZone | L3 + DataZone domain active |
| L5 | Lineage queryable by agents (MCP) | Not yet detected — questionnaire in v0.2 |

---

## 8. AI / Agentic Readiness

**Source module**: `src/dla/dimensions/ai_readiness.py`

| Level | Criteria | Detection in v0.1 |
|-------|----------|-------------------|
| L1 | No AI integrations | Default |
| L3 | Vector storage online | S3 Vectors index present OR OpenSearch vector collection present |
| L4 | Bedrock KBs over lake content | L3 + ≥1 KB with data sources OR ≥1 Bedrock agent |
| L5 | AgentCore Gateway + auto refresh | L4 + AgentCore gateway/runtime present |

---

## Modifying the rubric

Adding a new level criterion:
1. Add the detection signal to the relevant `src/dla/collectors/<service>.py` collector.
2. Update the threshold in `src/dla/dimensions/<dimension>.py`.
3. Update this rubric.
4. Add a remediation item in `src/dla/remediation/playbooks/<dimension>.yaml` keyed by `from_level: <new_threshold - 1>`.

Adding a whole new dimension is a heavier lift — see the project README for the four-step process.
