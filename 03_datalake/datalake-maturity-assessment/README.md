# datalake-maturity-assessment

> Programmatic scoring of an AWS data lake against eight maturity dimensions, with a prioritized, effort-weighted remediation roadmap that maps every finding back to a chapter of *Data Engineering with Generative and Agentic AI on AWS* (Apress, 2026).

Most "data lake maturity assessments" are slide-deck questionnaires someone fills out from memory. This one inspects your actual AWS account — buckets, tables, Lake Formation grants, KMS keys, Glue jobs, Knowledge Bases, S3 Vectors, and more — and produces a defensible, evidence-backed score.

It is built for three audiences:

- **Data engineering teams** preparing for an AI/agentic transition who want an honest baseline before they spend.
- **Platform leaders** justifying investment to a CFO or operating partner with data, not vibes.
- **PE operating teams and consultants** running diligence or post-close modernization on portfolio company data infrastructure.

This is the v0.1 implementation: collectors, scoring, remediation engine, and CLI all working against a live account in read-only mode.

---

## The eight dimensions

| # | Dimension | What it measures |
|---|-----------|------------------|
| 1 | **Storage & Table Format** | Use of open table formats (Iceberg/Delta/Hudi), S3 Tables, partitioning, medallion structure |
| 2 | **Catalog & Schema Management** | Glue Data Catalog coverage, column descriptions, DataZone publication, schema evolution discipline |
| 3 | **Access Control & Security** | KMS posture, Lake Formation grants, LF-Tag-based access control, public-access blocks |
| 4 | **Data Quality & Observability** | Deequ/Great Expectations integration, freshness SLOs, anomaly alerting |
| 5 | **Performance & Ingestion** | Scheduled batch, CDC, streaming ingestion; partition pruning; materialized views |
| 6 | **Cost & Lifecycle Governance** | Tagging, lifecycle rules, cost allocation, anomaly detection |
| 7 | **Lineage & Provenance** | Glue/OpenLineage emission, DataZone column-level lineage |
| 8 | **AI / Agentic Readiness** | Vector storage, Knowledge Bases, MCP servers, AgentCore Gateway |

Each dimension is scored 1–5. The report shows both a **weighted average** and a **weakest-link** call-out so a strong average doesn't mask a critical gap.

---

## Quickstart

```bash
# Install (editable, in a virtualenv)
python3 -m venv .venv
.venv/bin/pip install -e .

# Run a scan against the active AWS account (uses default profile + region)
.venv/bin/dla scan --region us-west-2 --output report.md --json-output report.json

# CI/CD mode — fail the build if score drops below target
.venv/bin/dla scan --region us-west-2 --min-score 3.0

# Filter to quick wins only (effort=S, impact≥3)
.venv/bin/dla scan --region us-west-2 --quick-wins

# Apply an industry weights preset
.venv/bin/dla scan --region us-west-2 --preset pe_portco
.venv/bin/dla list-presets
```

Required IAM permissions are in [`infrastructure/iam-policy.json`](infrastructure/iam-policy.json). The scanner makes only `List*`, `Get*`, `Describe*` calls — no mutations.

---

## Sample output

Run against AWS account `123456789012` / `us-west-2` on 2026-05-16:

```
─── Data Lake Maturity Assessment — account 123456789012 / region us-west-2 ────

  → Collecting S3 inventory and bucket settings ... done
  → Inspecting Glue Data Catalog, jobs, and crawlers ... done
  → Checking Lake Formation grants and LF-Tags ... done
  → Probing S3 Tables (managed Iceberg) ... done
  → Auditing KMS key inventory ... done
  → Sampling Athena workgroups and query history ... done
  → Listing Bedrock Knowledge Bases and Agents ... done
  → Probing S3 Vectors ... done
  → Listing OpenSearch domains/collections ... done
  → Probing Amazon DataZone ... done
  → Probing Bedrock AgentCore ... done
  → Reading Cost Explorer ... done
  → Listing CloudTrail trails ... done

  Scorecard — weighted 1.75 / 5.0 (Repeatable)

   #  Dimension                       Level    Top gap
   1  Storage & Table Format          L3 ✓     Schedule maintenance / migrate to S3 Tables
   2  Catalog & Schema Management     L2 ⚠     4 tables lack column descriptions
   3  Access Control & Security       L2 ⚠     Move to SSE-KMS + Lake Formation grants
   4  Data Quality & Observability    L2 ⚠     No Deequ/GE in any Glue job
   5  Performance & Ingestion         L1 🔴    No scheduled batch ingestion detected
   6  Cost & Lifecycle Governance     L2 ⚠     Cost allocation tags not active
   7  Lineage & Provenance            L1 🔴    No lineage tracking at all
   8  AI / Agentic Readiness          L1 🔴    No vector storage, KBs, or AgentCore

  Weakest link: L1 (Performance & Ingestion, Lineage, AI Readiness)
  Remediation items: 8, ≈18 person-weeks, 3 quick wins.

  ✔ Markdown report:   report.md
  ✔ JSON report:       report.json
  ✔ Remediation YAML:  remediation.yaml
```

The generated `report.md` includes per-dimension narrative with evidence excerpts, the prioritized remediation backlog, and a recommended chapter reading order driven by the lowest-scoring dimensions.

---

## Sample remediation report

Excerpted from the same scan above. The full report is ~310 lines; what follows is the structure and a few representative items.

### Weighted summary

> **Weighted Score**: **1.75 / 5.0** — Repeatable
> **Weakest Link**: L1 (Performance & Ingestion, Lineage & Provenance, AI / Agentic Readiness)
> **Remediation backlog**: 8 items, ≈18 person-weeks, **3 quick wins**

### Evidence excerpts

```markdown
### Access Control & Security — L2 ⚠
- Lake buckets inspected: 21 (encrypted: 21, KMS: 3, CMK-backed: 3, strict-PAB: 21)
- Customer-managed KMS keys in account: 3
- Lake Formation: 3 admin(s), 0 permissions (0 column-level, 0 LF-Tag-based), 3 LF-Tags
- IAM_ALLOWED_PRINCIPALS default permission: removed (LF enforced)

_Top gap_: Switch to SSE-KMS with a customer-managed key, then enable Lake Formation grants.

### Cost & Lifecycle Governance — L2 ⚠
- Lake buckets inspected: 21, tagged: 13, with lifecycle rules: 3
- Lifecycle transitions: Intelligent-Tiering=0, Glacier-family=0
- Active cost allocation tags: 2 (no data-product/owner tags activated)
- Cost anomaly subscriptions: 0; last 30d S3 spend ≈ $4.44

_Top gap_: Activate cost allocation tags (DataProduct, Owner, BusinessUnit) and apply them to lake buckets/tables.
```

### Remediation backlog (sorted by `priority_score = impact / effort_weeks`)

| ID | Title | Dim | L→L | Impact | Effort | Priority | Tag |
|----|-------|-----|-----|:--:|:--:|:--:|---|
| **REM-006** | Activate cost allocation tags + apply DataProduct/Owner | Cost | 2→3 | 5 | S (~1w) | **5.0** | `quick win` |
| **REM-001** | Schedule Iceberg maintenance: OPTIMIZE, EXPIRE, REMOVE_ORPHANS | Storage | 3→4 | 4 | S (~1w) | **4.0** | `quick win` |
| **REM-002** | Auto-generate column descriptions via Bedrock | Catalog | 2→3 | 4 | S (~1w) | **4.0** | `quick win` |
| REM-003 | Switch to SSE-KMS + CMKs, enable Lake Formation grants | Security | 2→3 | 5 | M (~3w) | 1.67 | |
| REM-004 | Integrate Deequ or Great Expectations into ≥50% of pipelines | Quality | 2→3 | 5 | M (~3w) | 1.67 | |
| REM-005 | Stand up scheduled batch ingestion (Glue/MWAA) | Performance | 1→2 | 5 | M (~3w) | 1.67 | |
| REM-007 | Enable Glue Catalog lineage + emit OpenLineage | Lineage | 1→2 | 5 | M (~3w) | 1.67 | |
| REM-008 | Bring vector storage online (S3 Vectors / OpenSearch) | AI Readiness | 1→2 | 5 | M (~3w) | 1.67 | |

### Anatomy of a single remediation item

Each item in the backlog is rendered with its own steps, acceptance criteria, AWS service list, book reference, and (where applicable) a pointer to a companion project in this repo:

```markdown
### REM-001 — Schedule Iceberg maintenance: OPTIMIZE, EXPIRE_SNAPSHOTS, REMOVE_ORPHAN_FILES  `quick win`

**Dimension**: Storage & Table Format (L3 → L4)   **Impact**: 4/5   **Effort**: S (≈1 wk)   **Priority**: 4.0
**AWS services**: AWS Glue, Amazon EventBridge, AWS Lambda
**Book**: Chapter 3 — Data Lake Design with Apache Iceberg and S3 Tables

**Why it matters**

Unmaintained Iceberg tables silently grow small files and snapshots until
query performance and storage cost both degrade. Maintenance is the
difference between an Iceberg lake and an Iceberg swamp.

**Steps**
- Adopt iceberg-maintenance-agent; configure thresholds.
- Wire Glue scheduled triggers to invoke OPTIMIZE/EXPIRE/REMOVE_ORPHANS weekly.
- Alarm on small-file ratio > 0.5.

**Acceptance criteria**
- Each Iceberg table has a scheduled maintenance run.
- Orphan-file count trends down across 4 weekly runs.

**Reference code**: `../../iceberg-maintenance-agent/`
```

### Recommended reading order

The report ends with a chapter list driven by the lowest-scoring dimensions:

> 1. **Chapter 5 + Chapter 9** — Streaming and Real-Time Data Processing — closes Performance & Ingestion (L1).
> 2. **Chapter 4** — Data Mesh with Amazon DataZone — closes Lineage & Provenance (L1).
> 3. **Chapter 8 + Chapter 12** — RAG with S3 Vectors, Building AI Agents with Bedrock AgentCore — closes AI/Agentic Readiness (L1).

The 3 quick wins together — cost tags, Iceberg maintenance, Bedrock column descriptions — buy roughly +0.6 weighted-score uplift in ~3 person-weeks before any of the medium-effort items start.

A machine-readable `remediation.yaml` is emitted alongside the Markdown so the backlog can be imported into a planning tool (Jira, Linear, GitHub Projects).

---

## Architecture

```
                    ┌────────────────────────────────┐
                    │           CLI (dla scan)       │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │           Collectors           │
                    │  S3 · Glue · LakeFormation     │
                    │  S3 Tables · KMS · CloudTrail  │
                    │  Athena · Bedrock KB · Cost    │
                    │  Explorer · DataZone · Vectors │
                    │  · OpenSearch · AgentCore      │
                    └──────────────┬─────────────────┘
                                   │ read-only
                    ┌──────────────▼─────────────────┐
                    │      Account Snapshot (dict)   │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │  8 Dimension scorers + weights │
                    └──────────────┬─────────────────┘
                                   │
            ┌──────────────────────┼────────────────────────┐
            ▼                      ▼                        ▼
   ┌─────────────────┐   ┌──────────────────┐    ┌──────────────────┐
   │ report.md       │   │ remediation.yaml │    │ report.json      │
   └─────────────────┘   └──────────────────┘    └──────────────────┘
```

Collectors are tolerant: any per-API failure (AccessDenied, region-not-supported, service-not-launched) is logged and the scan continues. Dimensions that don't get their data degrade rather than abort the whole run.

---

## Repository layout

```
datalake-maturity-assessment/
├── README.md
├── pyproject.toml
├── src/dla/
│   ├── cli.py                       Click CLI entrypoint (`dla`)
│   ├── runner.py                    Orchestrates collectors → scoring → remediation
│   ├── collectors/                  One module per AWS service group
│   │   ├── s3.py
│   │   ├── glue.py
│   │   ├── lakeformation.py
│   │   ├── s3tables.py
│   │   ├── kms.py
│   │   ├── athena.py
│   │   ├── bedrock_kb.py
│   │   ├── s3_vectors.py
│   │   ├── opensearch.py
│   │   ├── datazone.py
│   │   ├── agentcore.py
│   │   ├── cost_explorer.py
│   │   └── cloudtrail.py
│   ├── dimensions/                  Pure-function scorers, snapshot → level + evidence
│   │   ├── storage_format.py
│   │   ├── catalog_schema.py
│   │   ├── access_security.py
│   │   ├── quality_observability.py
│   │   ├── performance_ingestion.py
│   │   ├── cost_lifecycle.py
│   │   ├── lineage.py
│   │   └── ai_readiness.py
│   ├── scoring/rules.py             Weight loading, weighted average, weakest-link
│   ├── remediation/
│   │   ├── catalog.py               Loads playbook YAML, ranks by impact/effort
│   │   └── playbooks/               One YAML per dimension (items keyed by from_level)
│   └── reporting/
│       ├── markdown.py
│       └── json_report.py
├── config/
│   ├── default_weights.yaml
│   └── industry_presets/
│       ├── pe_portco.yaml
│       ├── financial_services.yaml
│       └── healthcare.yaml
├── infrastructure/
│   └── iam-policy.json              Read-only assessment policy
├── docs/
│   └── dimensions.md                Full rubric + detection logic
└── tests/                           (placeholder for v0.2)
```

---

## Detection vs. self-assessment

| Dimension | Programmatic coverage in v0.1 |
|-----------|-------------------------------|
| Storage & Table Format | High — Glue table_type, file format sampling, S3 Tables presence |
| Catalog & Schema Management | High — table count, column-description coverage, DataZone domains |
| Access Control & Security | High — bucket encryption, KMS, LF permissions, LF-Tags, PAB |
| Data Quality & Observability | Medium — Glue job script signatures (Deequ/GE) |
| Performance & Ingestion | Medium — Glue triggers, partition keys, Athena query filter share |
| Cost & Lifecycle Governance | High — bucket tags, lifecycle rules, CE active tags, anomaly subs |
| Lineage & Provenance | Medium — Glue lineage config, OpenLineage signal in jobs, DataZone |
| AI / Agentic Readiness | High — KBs, S3 Vectors, OpenSearch vector engine, AgentCore |

A `--interactive` questionnaire mode for the medium-coverage gaps is planned for v0.2 (see roadmap).

---

## Mapping to the book

Every remediation item references the chapter that explains the underlying pattern:

| Dimension | Primary chapter |
|-----------|-----------------|
| Storage & Table Format | Chapter 3 — Data Lake Design with Apache Iceberg and S3 Tables |
| Catalog & Schema Management | Chapter 4 — Data Mesh with Amazon DataZone |
| Access Control & Security | Chapter 2 — Data Security and Governance |
| Data Quality & Observability | Chapter 5 — Big Data Processing with AWS Glue and AI Agents |
| Performance & Ingestion | Chapters 5 + 9 |
| Cost & Lifecycle Governance | Chapter 2 (FinOps section) |
| Lineage & Provenance | Chapter 4 |
| AI / Agentic Readiness | Chapters 8 + 12 |

Each report ends with a **Recommended reading order** section listing the three chapters that address the lowest-scoring dimensions first.

Several remediation items also link to companion projects in this repo:

- `iceberg-time-travel-debugger/` — referenced by Storage L2→L3
- `iceberg-maintenance-agent/` — referenced by Storage L3→L4
- `auto-cataloger/` — referenced by Catalog L2→L3
- `datazone-as-code/` — referenced by Catalog L3→L4 and Lineage L3→L4
- `lakeformation-bootstrap/` — referenced by Security L2→L3
- `lakeformation-data-filters/` — referenced by Security L4→L5

---

## Roadmap

- **v0.1** ✅ Eight dimensions detect-only against a single account.
- **v0.2** Interactive questionnaire mode for the medium-coverage detection gaps.
- **v0.3** GitHub Action with PR-diff comments.
- **v0.4** Industry presets (PE portco, financial services, healthcare) — partial in v0.1.
- **v0.5** QuickSight dashboard template for multi-account rollup.
- **v0.6** Cross-account aggregation for AWS Organizations and PE portfolio views.
- **v0.7** Anonymized benchmark publication.
- **v0.8** Agentic mode: an MCP server exposing the assessment so a Strands agent can answer "what's the highest-leverage change I can make this quarter?"

---

## Companion to the book

This project is a companion to *Data Engineering with Generative and Agentic AI on AWS* (Apress, 2026). Every dimension corresponds to chapters in the book that teach the underlying patterns; running the assessment is the most efficient way to translate the book into a prioritized action plan for your specific environment.
