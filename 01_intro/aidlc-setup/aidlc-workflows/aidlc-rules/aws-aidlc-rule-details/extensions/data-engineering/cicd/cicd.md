# CI/CD for Data Assets Extension

Rules for applying software engineering delivery practices to data assets — including schema change review, breaking change detection, pipeline deployment promotion, secret scanning, and environment parity. Applies to any project that produces data pipelines, schemas, dbt models, Glue jobs, or orchestration definitions deployed to a production environment.

This extension layers on top of `data-engineering/baseline`. It composes with `orchestration` (deployment targets), `s3-lakehouse` (table format migrations), `databricks` (DABs / Asset Bundles), `redshift`, `snowflake`, and `postgresql` (DDL migration tooling).

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule CICD-01: All Data Assets Managed in Version Control

**Severity**: P0 — Blocking

**Rule**: Every artifact that defines the shape or behavior of a production data asset MUST be committed to version control: DDL migration scripts, schema definitions, dbt models, Glue job scripts, DAG files, State Machine ASL definitions, IaC (CloudFormation, CDK, Terraform), and data contracts. Assets that exist only in a UI, console, or metastore and cannot be reproduced from the repository are prohibited in production. This rule enables all downstream CI/CD, review, and audit rules.

**Verification**:
- Code Generation places all pipeline code, DDL, and IaC in the repository under documented paths.
- No production asset is created exclusively via a cloud console, Airflow UI, or notebook run.
- Build and Test includes a `git status` / drift-detection check confirming deployed state matches repository HEAD.
- Brownfield reverse engineering captures any existing assets not yet in version control and creates a remediation plan.

---

## Rule CICD-02: Schema Changes Require a Pull Request with Reviewer Sign-Off

**Severity**: P0 — Blocking

**Rule**: Any change to a published schema — adding, removing, or modifying columns; changing types, nullability, or partitioning; altering primary or foreign key constraints — MUST be made via a pull request and approved by at least one designated data owner or schema reviewer before merge to the default branch. Direct commits to the default branch that modify schema artifacts are prohibited. This ensures breaking change classification (DATAENG-02) is reviewed by a human before the change lands.

**Verification**:
- Infrastructure Design or build instructions document the branch protection rule: required reviewers on paths matching schema artifacts (`migrations/`, `models/`, `schemas/`, `dags/`, `*.sql`, `*.yaml` schema files, etc.).
- Code Generation places schema changes in discrete migration files, not inline with unrelated code changes.
- Build and Test confirms branch protection is configured (e.g., via `gh api` check or repository settings audit step).
- Reviewer sign-off is captured in the PR; the merge commit SHA is recorded in audit.md.

---

## Rule CICD-03: Breaking Change Detection Runs Automatically in CI

**Severity**: P0 — Blocking

**Rule**: The CI pipeline MUST automatically detect breaking schema changes on every pull request that touches schema artifacts and surface the result before merge. A breaking change detected in CI is a hard blocker: the PR cannot merge until either the design is made additive, or the breaking change is explicitly classified and a consumer coexistence plan is approved (per DATAENG-02). Acceptable detection mechanisms include: schema diff tools (buf breaking, schemathesis, dbt schema diff), Glue Schema Registry compatibility checks, Iceberg/Delta schema evolution validation, or a custom migration linter.

**Verification**:
- Infrastructure Design or build instructions name the breaking change detection tool and the CI step it runs in.
- CI configuration (e.g., `.github/workflows/`, `buildspec.yml`, `Jenkinsfile`) includes the schema diff step on the paths that trigger it.
- Build and Test documents a test case where a deliberately breaking change (column removal, type narrowing) causes the CI step to fail.
- A breaking change that bypasses detection (e.g., renaming a file to avoid path matching) is itself a CI defect — path matching covers all schema artifact patterns.

---

## Rule CICD-04: Migration Scripts Are Forward-Only and Sequenced

**Severity**: P0 — Blocking

**Rule**: Schema changes MUST be expressed as versioned, forward-only migration scripts (Flyway, Liquibase, Alembic, custom numbered SQL files). In-place edits to previously applied migration files are prohibited — once a migration is merged and applied to any environment it is immutable. Each migration file MUST have a unique monotonically increasing version or timestamp prefix. Rollback scripts are permitted as a separate forward migration (e.g., `V003__revert_column_rename.sql`) but MUST NOT be applied automatically.

**Verification**:
- Code Generation produces migration files following the project's chosen versioning scheme.
- CI includes a lint step that detects modifications to already-merged migration files (e.g., `git diff origin/main -- migrations/` checks for edits to existing files rather than new additions).
- Build and Test applies migrations to a clean schema and verifies the target state matches the contract.
- No migration file is deleted or modified after merging to the default branch.

---

## Rule CICD-05: CI Pipeline Applies Migrations to an Isolated Test Schema

**Severity**: P1 — Warning

**Rule**: The CI pipeline MUST apply all pending migrations against an isolated, ephemeral test schema or database before the pull request can merge. Testing migrations only in staging or production is prohibited. The test schema MUST be spun up fresh for each CI run to catch ordering dependencies and cumulative state issues that a diff-only review misses.

**Verification**:
- Build instructions include a step: spin up ephemeral schema, apply all migrations from V0, run validation queries, tear down.
- CI uses a real database engine matching production (not a SQLite substitute for a PostgreSQL target, not a local Spark session for a Redshift target).
- Test schema is isolated per PR run (unique name derived from PR number or commit SHA) to prevent concurrent CI runs from interfering.
- Migration apply step fails the CI run on any error; partial applies are not accepted.

---

## Rule CICD-06: Environment Promotion Is Sequential and Gated

**Severity**: P0 — Blocking

**Rule**: Pipeline and schema artifacts MUST promote through environments in a fixed sequence (e.g., dev → staging → production) with an explicit gate at each boundary. Deploying directly to production without a prior successful staging deployment is prohibited except during declared incidents with documented approval. Each promotion gate MUST require: CI passing on the source branch, migration apply success in the target environment's test schema, and a human approval step for the production gate.

**Verification**:
- Infrastructure Design diagrams the promotion sequence and gate requirements per environment.
- The CI/CD pipeline configuration enforces the sequence (no production deploy job unless staging succeeded).
- The production gate requires manual approval (GitHub Actions environment protection, CodePipeline approval action, or equivalent).
- Incident-exception deployments are logged in audit.md with approver identity and timestamp.

---

## Rule CICD-07: Secret Scanning Blocks Merge on Detection

**Severity**: P0 — Blocking

**Rule**: Secret scanning MUST run on every pull request and block merge if a secret pattern is detected. Scanning MUST cover: AWS access keys, Snowflake connection strings, database passwords, API tokens, private keys, and generic high-entropy strings. Approved scanning tools include: GitHub Advanced Security secret scanning, GitLeaks, Trufflehog, detect-secrets, or AWS CodeGuru. A finding that is suppressed without review is itself a policy violation. This composes with DATAENG-15.

**Verification**:
- Build instructions include the secret scanning tool and the CI step it runs on.
- A false-positive suppression requires a documented review comment on the suppression entry (e.g., `.secrets.baseline` entry with justification).
- Build and Test documents a test case where a synthetic secret pattern (e.g., a clearly fake `AKIA`-prefixed key) causes the scan to fail.
- Pre-commit hooks for secret scanning are recommended for developer workstations (P2 advisory) but CI gate is the enforced control.

---

## Rule CICD-08: Pipeline Code Is Tested Before Deployment

**Severity**: P1 — Warning

**Rule**: Data pipeline code MUST have automated tests that run in CI before deployment: unit tests for transformation logic, DAG import tests for MWAA DAGs (per ORCH-02), ASL schema validation for Step Functions definitions, and contract tests asserting that the pipeline's output schema matches the declared data contract. Integration tests against a real compute target are required before staging promotion. Code with no tests cannot be deployed to staging or production.

**Verification**:
- Build and Test includes: unit test step, DAG import test or ASL validation step, and at least one contract test per produced dataset.
- CI configuration runs tests before any deploy step.
- Code coverage is reported; a floor (recommended: 70% for transformation logic) is documented even if not enforced as a hard gate.
- Brownfield projects document existing test gaps and a remediation timeline.

---

## Rule CICD-09: Deployed Artifact Versions Are Tagged and Traceable

**Severity**: P1 — Warning

**Rule**: Every artifact deployed to staging or production MUST be tagged with its originating commit SHA, CI run ID, and deployment timestamp. Deployed Lambda functions, Glue job scripts, DAG S3 objects, Step Functions State Machine revisions, dbt manifest files, and container images MUST all carry this provenance. Untagged production deployments make incident root-cause analysis impractical. This composes with ORCH-14.

**Verification**:
- Infrastructure Design specifies the tagging strategy and which resources carry provenance tags.
- CI/CD pipeline sets tags at deploy time (AWS resource tags, S3 object metadata, image labels).
- Build and Test includes a post-deploy step that asserts the deployed artifact's provenance tags match the current CI run.
- dbt manifest.json or equivalent artifact version file is stored in S3 or an artifact store alongside each production deployment.

---

## Rule CICD-10: Data Contract Changes Follow the Same PR Process as Schema Changes

**Severity**: P1 — Warning

**Rule**: Changes to data contract files (per DATAENG-01) MUST follow the same PR and reviewer sign-off process as schema migration scripts (CICD-02). A data contract change that is not reflected in a corresponding schema migration is a finding. A schema migration that is not reflected in a corresponding data contract update is a finding. Contracts and migrations MUST remain in sync; CI MUST detect divergence.

**Verification**:
- CI includes a sync-check step: if a migration file changes, the corresponding contract file must also change in the same PR (and vice versa), unless the change is explicitly classified as contract-only (e.g., documentation update) or migration-only (e.g., index addition with no schema change).
- Functional Design or Code Generation co-locates contract files with the migration or model they describe, making divergence visible in diffs.
- Build and Test validates that the applied migration's resulting schema matches the contract's column list, types, and nullability for each affected dataset.
