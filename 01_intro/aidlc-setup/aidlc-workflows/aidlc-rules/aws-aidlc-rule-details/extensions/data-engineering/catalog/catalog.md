# Data Catalog and Ownership Extension

Rules for ensuring every production dataset is discoverable, has a declared owner, and carries the metadata needed for consumers to find, evaluate, and trust it without reading source code or asking the producing team. Applies to any pipeline producing a dataset consumed by another team, service, or analytical workload.

This extension layers on top of `data-engineering/baseline`. It composes with `s3-lakehouse` (Glue Catalog as the technical metastore), `databricks` (Unity Catalog), `snowflake` (Snowflake Information Schema and data sharing), `redshift` (Glue Catalog + Redshift Serverless catalog), and `cicd` (catalog registration as part of deployment).

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule CAT-01: Every Production Dataset Has a Declared Owner

**Severity**: P0 — Blocking

**Rule**: Every dataset produced for consumption by another team, service, or analytical workload MUST have a declared owner: a named team (not an individual) responsible for schema stability, SLA adherence, quality, and incident response. Ownership MUST be recorded in two places: the data contract (DATAENG-01) and the catalog entry (CAT-02). A dataset with no owner is an unowned liability — consumers have no escalation path and the pipeline can be silently abandoned. Individual-person ownership is prohibited because it creates a single point of failure.

**Verification**:
- Requirements Analysis names the owning team per dataset alongside the data contract.
- The catalog entry for each dataset carries an `owner` field referencing a team identifier (email alias, Slack channel, PagerDuty service, or equivalent).
- Functional Design identifies any dataset changing ownership and captures the handoff procedure.
- Build and Test includes a post-deploy check asserting the `owner` field is non-empty on every registered dataset.

---

## Rule CAT-02: Every Production Dataset Has a Catalog Entry Before Go-Live

**Severity**: P0 — Blocking

**Rule**: Every dataset promoted to production MUST have a catalog entry registered before or at the time of first production write. A catalog entry is not optional documentation — it is the mechanism by which consumers discover the dataset, evaluate its fitness, and find its owner. Registration MUST occur as part of the deployment pipeline, not as a post-launch manual step. Acceptable catalog targets: AWS Glue Data Catalog, Unity Catalog, Apache Atlas, DataHub, Collibra, Alation, or any catalog that supports programmatic registration and is queryable without reading source code.

**Verification**:
- Infrastructure Design names the catalog target and the registration mechanism (Glue Crawler, Unity Catalog DDL, DataHub ingestion source, etc.).
- Code Generation or IaC includes catalog registration as a deployment step, not a separate manual runbook.
- Build and Test asserts the catalog entry exists and is populated after a staging deployment.
- The catalog entry references the data contract artifact (DATAENG-01) by URI or version identifier.

---

## Rule CAT-03: Catalog Entry Carries a Minimum Required Metadata Set

**Severity**: P0 — Blocking

**Rule**: Every catalog entry MUST carry the following fields at registration time. Missing fields are a blocking finding at the Build and Test gate:

| Field | Requirement |
|-------|-------------|
| `display_name` | Human-readable name, distinct from the technical table name |
| `description` | Plain-language summary of what the dataset contains and its intended use |
| `owner` | Team identifier per CAT-01 |
| `classification` | Data sensitivity level per DATAENG-06 (`public`, `internal`, `confidential`, `pii`, `phi`, `pci`) |
| `freshness_sla` | How current the data is expected to be (e.g., "updated daily by 08:00 UTC") |
| `schema_version` | Semantic version of the data contract (per DATAENG-01) |
| `data_contract_uri` | Link or path to the full data contract artifact |
| `status` | Lifecycle state: `experimental`, `stable`, `deprecated`, `sunset_date` |
| `producing_pipeline` | Name or ARN of the job or DAG that writes the dataset |

**Verification**:
- Functional Design includes a catalog metadata template populated for each dataset.
- Code Generation or IaC writes these fields to the catalog at deploy time.
- Build and Test runs a metadata completeness check against the catalog API after staging deployment; any missing required field fails the gate.
- `status = experimental` is acceptable for initial launch but must be upgraded to `stable` before the dataset is referenced by any downstream production pipeline.

---

## Rule CAT-04: Deprecated Datasets Are Cataloged With a Sunset Date

**Severity**: P1 — Warning

**Rule**: When a dataset is scheduled for retirement, its catalog entry MUST be updated to `status = deprecated` with a `sunset_date` field set at least 30 days in the future. Consumers listed in the data contract (DATAENG-01) MUST be notified at deprecation time. Silent deletion of a production dataset — removing the table or stopping the pipeline without a catalog deprecation notice — is prohibited. After the sunset date, the entry transitions to `status = removed` and is retained for 90 days before deletion to preserve lineage history.

**Verification**:
- Requirements Analysis captures the sunset plan whenever a dataset is being retired.
- The catalog entry is updated to `deprecated` with `sunset_date` before any pipeline change is deployed.
- Downstream consumers named in the data contract receive a deprecation notice (email, Slack, Jira ticket, or equivalent) captured in audit.md.
- Build and Test includes a check that no pipeline in staging or production reads from a dataset with `status = removed`.

---

## Rule CAT-05: Catalog Entries Updated Automatically on Schema Change

**Severity**: P1 — Warning

**Rule**: Schema changes deployed via the migration pipeline (CICD-04) MUST trigger an automated catalog update. Catalog entries that lag behind the deployed schema are misleading — consumers make decisions based on stale metadata. The update MUST occur in the same deployment pipeline run as the migration apply, not as a separate manual step. This composes with CICD-09: the catalog update is a deployable artifact with the same provenance tagging as the migration.

**Verification**:
- Infrastructure Design names the mechanism that keeps the catalog in sync with deployed schema (e.g., post-migration hook, Glue Crawler schedule tight enough to reflect changes within one deployment cycle, Unity Catalog DDL auto-updates, DataHub ingestion triggered by CI).
- Build and Test asserts that after a migration apply, the catalog reflects the new schema within the deployment pipeline run.
- A deliberately lagging catalog (e.g., a weekly crawler on a daily-changing schema) is flagged as a finding.

---

## Rule CAT-06: Ownership Review Cadence Defined

**Severity**: P2 — Advisory

**Rule**: Dataset ownership MUST be reviewed on a defined cadence (recommended: quarterly) to catch orphaned datasets whose owning team has been reorganized or disbanded. The review process MUST produce one of three outcomes per dataset: ownership confirmed, ownership transferred to a new team, or dataset deprecated per CAT-04. Datasets that have not been reviewed within two cadence periods are flagged as ownership-unknown and blocked from new consumer onboarding until reviewed.

**Verification**:
- Infrastructure Design or operational runbooks document the review cadence and responsible party.
- The catalog supports a `last_ownership_reviewed` timestamp field; it is updated after each review.
- A catalog query surfacing datasets with `last_ownership_reviewed` older than two cadence periods is documented as an operational monitor.

---

## Rule CAT-07: Sensitive Dataset Discovery Is Access-Controlled

**Severity**: P0 — Blocking

**Rule**: Catalog entries for datasets classified as `pii`, `phi`, or `pci` (per DATAENG-06) MUST be visible in search results only to principals with a legitimate access need. The existence of a PII dataset MUST NOT be discoverable by all employees — catalog-level visibility must mirror data-level access controls. This does not mean classified datasets are invisible; it means the catalog enforces the same access model as the underlying data. This composes with S3LH-08 (Lake Formation), DBX-01 (Unity Catalog column masks), and SF-08 (Snowflake masking policies).

**Verification**:
- Infrastructure Design specifies catalog access tiers: which roles can discover `pii`/`phi`/`pci` entries vs `internal` vs `public` entries.
- Catalog access controls are provisioned in the same IaC as data access controls — they are not managed separately.
- Build and Test includes a check that a principal without data access cannot retrieve the catalog entry for a classified dataset.
- `classification` field (CAT-03) drives the access tier assignment; no manual per-entry override without documented justification.

---

## Rule CAT-08: Lineage Links Are Populated in the Catalog

**Severity**: P1 — Warning

**Rule**: The catalog entry for every produced dataset MUST reference its upstream sources and the transformation that produced it, queryable from the catalog without reading pipeline code. Lineage in the catalog need not be column-level (that is governed by DATAENG-08) but MUST capture dataset-level lineage: which datasets feed this one, and which datasets consume it. This makes impact analysis — "what breaks if I change dataset X?" — answerable from the catalog alone.

**Verification**:
- Infrastructure Design names the lineage integration between the catalog and the lineage mechanism named in DATAENG-08 (OpenLineage, Unity Catalog lineage, Snowflake ACCESS_HISTORY, dbt artifacts, etc.).
- Catalog entries for produced datasets include at least one upstream source reference and at least one downstream consumer reference where consumers are known.
- Build and Test verifies at least one dataset's catalog entry contains a populated lineage graph after a staging deployment.
- Lineage entries are updated by the same automated mechanism as schema updates (CAT-05) — not maintained manually.
