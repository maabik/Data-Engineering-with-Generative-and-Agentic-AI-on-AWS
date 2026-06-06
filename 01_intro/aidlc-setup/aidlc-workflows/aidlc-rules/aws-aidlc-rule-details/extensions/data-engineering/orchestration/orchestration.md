# Orchestration Extension

Rules for data pipeline orchestration on AWS using Amazon Managed Workflows for Apache Airflow (MWAA) and AWS Step Functions. Covers DAG design, environment configuration, IAM, error handling, scheduling, and observability for both orchestrators.

This extension layers on top of `data-engineering/baseline`. It composes with all compute and storage extensions (`s3-lakehouse`, `databricks`, `redshift`, `snowflake`, `postgresql`) — orchestration rules govern the control plane regardless of what the pipelines execute.

## Severity Tiers

Severity tiers are defined in `data-engineering/baseline`. The same P0/P1/P2 model applies here:

| Tier | Enforcement |
|------|-------------|
| **P0** | Blocking — must be resolved before stage sign-off. |
| **P1** | Warning — must be resolved before production go-live; flagged but does not block individual stages. |
| **P2** | Advisory — good practice; noted in compliance summary but non-blocking at all stages. |

---

## Rule ORCH-01: Orchestrator Choice Documented Per Pipeline

**Severity**: P1 — Warning

**Rule**: The orchestrator for every production pipeline MUST be explicitly chosen and documented with rationale. MWAA is preferred when: pipelines have complex conditional branching, dynamic task generation, sensor-based waits, or require rich cross-pipeline dependency tracking. Step Functions is preferred when: pipelines are serverless-native, integration with AWS SDK services is the primary workload, or minimal operational overhead is required. Mixed use of both orchestrators within a single logical workflow is prohibited unless the boundary and handoff mechanism are explicitly designed.

**Verification**:
- Functional Design or Workflow Planning names the orchestrator per pipeline with rationale.
- Pipelines that span both MWAA and Step Functions document the exact handoff (e.g., MWAA Task invoking a State Machine via `StepFunctionExecuteTrigger`, or EventBridge routing).
- No pipeline uses both orchestrators to do the same work (e.g., retry logic in MWAA and Step Functions for the same task).

---

## Rule ORCH-02: DAG Design — No Business Logic in DAG Files (MWAA)

**Severity**: P0 — Blocking

**Rule**: MWAA DAG files MUST contain only orchestration logic (task definitions, dependencies, schedule, callbacks). Business logic, data transformations, SQL, and API calls MUST be implemented in external modules, operators, or compute targets (EMR, Glue, ECS, Lambda, Databricks). DAG files that import and execute transformation code directly are prohibited. This prevents DAG parse failures from corrupting the entire Airflow scheduler and keeps compute logic testable independently.

**Verification**:
- Code Generation places DAG files in a `dags/` directory containing only orchestration constructs.
- Business logic resides in a separate module, container, or compute service called via an operator.
- Build and Test includes a DAG import test (all DAGs load without error in an isolated Airflow environment).
- No SQL strings longer than a single-line identifier or templated reference appear directly in the DAG file.

---

## Rule ORCH-03: DAG Idempotency and Catchup Behavior Explicitly Set (MWAA)

**Severity**: P0 — Blocking

**Rule**: Every DAG MUST have `catchup` set explicitly (`catchup=True` or `catchup=False`) — relying on the Airflow global default is prohibited. DAGs with `catchup=True` MUST produce idempotent runs (per DATAENG-03) because catchup triggers multiple historical runs simultaneously. DAGs with `catchup=False` MUST document why historical backfill is not needed. `max_active_runs` MUST be set to prevent unbounded concurrent DAG runs.

**Verification**:
- Every DAG definition includes explicit `catchup=` and `max_active_runs=` parameters.
- DAGs with `catchup=True` have idempotency verified in Build and Test (per DATAENG-03).
- DAGs with `catchup=False` include a comment or design note explaining why backfill is not needed.

---

## Rule ORCH-04: Sensor and Trigger Design — No Indefinite Poke Loops (MWAA)

**Severity**: P1 — Warning

**Rule**: Sensors MUST use `mode="reschedule"` rather than `mode="poke"` for any wait exceeding 5 minutes to avoid holding a worker slot indefinitely. `timeout` MUST be set on every sensor; a sensor without a timeout can block a DAG run permanently. External task sensors MUST define `execution_timeout` and `failed_states` to detect upstream failures rather than hanging.

**Verification**:
- Code Generation sets `mode="reschedule"` and `timeout` on every sensor with expected wait > 5 minutes.
- No sensor omits a `timeout` value.
- `ExternalTaskSensor` definitions include `failed_states` and `execution_timeout`.

---

## Rule ORCH-05: MWAA Environment Sizing and Worker Autoscaling Configured

**Severity**: P1 — Warning

**Rule**: MWAA environment class and worker autoscaling bounds MUST be explicitly chosen and documented. Environment class (mw1.small through mw1.2xlarge) MUST be justified against the DAG parse time budget and concurrent task count. `min_workers` and `max_workers` MUST be set with a documented basis. The default `max_workers=10` is not an acceptable substitute for a deliberate sizing decision for production environments.

**Verification**:
- Infrastructure Design specifies environment class, `min_workers`, `max_workers`, and scheduler count with rationale.
- DAG parse time budget is evaluated (target < 30 seconds total; Airflow drops DAGs that parse too slowly).
- Airflow configuration overrides (`core.parallelism`, `scheduler.max_dagruns_to_create_tis_per_loop`, etc.) are set deliberately, not left at defaults.

---

## Rule ORCH-06: MWAA Secrets via AWS Secrets Manager Backend

**Severity**: P0 — Blocking

**Rule**: MWAA connections and variables that contain credentials MUST be stored in AWS Secrets Manager using the MWAA Secrets Manager backend, not in the Airflow metastore UI. Connection URIs and variable values stored in the metastore are accessible to any user with UI access and are not rotatable independently of Airflow. This rule composes with DATAENG-15.

**Verification**:
- Infrastructure Design enables the Secrets Manager backend (`secrets.backend = airflow.providers.amazon.aws.secrets.secrets_manager.SecretsManagerBackend`).
- Code Generation references connections by `aws_default` or named connection IDs that resolve from Secrets Manager.
- No production connection URI or password appears in the Airflow UI connection store or in DAG code.
- Rotation of any secret requires no DAG or environment change.

---

## Rule ORCH-07: State Machine Design — Each State Is Idempotent (Step Functions)

**Severity**: P0 — Blocking

**Rule**: Every Task state in a Step Functions State Machine MUST be idempotent — a re-execution of the state with the same input MUST produce the same outcome without side effects or duplicated side effects. Lambda functions invoked from Step Functions MUST use the `clientToken` or idempotency key pattern. AWS SDK integrations (Glue, EMR, ECS, Batch) MUST check for an already-running or already-completed execution before starting a new one when the state is retried. This composes with DATAENG-03.

**Verification**:
- Functional Design documents the idempotency mechanism per Task state (clientToken, existence check, UPSERT target, etc.).
- Code Generation implements idempotency checks in Lambda handlers and SDK integration states.
- Build and Test includes a scenario where the State Machine is re-executed from a mid-workflow failure and asserts no duplicate side effects.

---

## Rule ORCH-08: Step Functions Retry and Catch Blocks on Every Task State

**Severity**: P0 — Blocking

**Rule**: Every Task state in a production State Machine MUST have an explicit `Retry` block and a `Catch` block. States without `Retry` propagate transient AWS SDK errors (throttling, eventual-consistency races) as permanent failures. States without `Catch` leave executions stuck in a terminal `Failed` state with no routing to dead-letter or alerting paths. This composes with DATAENG-13.

**Verification**:
- Every Task state in the ASL definition has at least one `Retry` entry covering `States.TaskFailed` and `States.Timeout`.
- Retry `IntervalSeconds`, `MaxAttempts`, and `BackoffRate` are set deliberately (not defaulted to MaxAttempts=3 without review).
- Every Task state has a `Catch` block routing permanent failures to a failure state that notifies via SNS or EventBridge (composes with DATAENG-09).
- Build and Test includes an induced-failure scenario that exercises the Catch path and verifies notification delivery.

---

## Rule ORCH-09: Step Functions Express vs Standard Workflow Decision Documented

**Severity**: P1 — Warning

**Rule**: The workflow type (Standard or Express) MUST be chosen explicitly and documented with rationale. Standard Workflows are required when: execution history must be auditable via the console or `GetExecutionHistory`, executions may run longer than 5 minutes, or exactly-once execution semantics are required. Express Workflows are appropriate for high-volume, short-duration (< 5 minutes), at-least-once workloads. Mixing workflow types within a logical pipeline requires documented handoff.

**Verification**:
- Infrastructure Design specifies Standard or Express for each State Machine with rationale aligned to duration, volume, and auditability requirements.
- Express Workflows have execution logs routed to CloudWatch Logs (the only audit mechanism; console history is not available).
- Standard Workflows that run longer than 1 hour document the expected execution duration and verify it is within the 1-year limit.

---

## Rule ORCH-10: Execution Input and Output Payload Size Managed (Step Functions)

**Severity**: P1 — Warning

**Rule**: State Machine execution input, state output, and inter-state payload MUST stay within AWS limits (256 KB per state input/output for Standard; same for Express). Large datasets MUST be passed by reference (S3 URI, DynamoDB key, SSM Parameter ARN) rather than by value. Workflows that inline dataset contents in the execution payload are prohibited.

**Verification**:
- Functional Design identifies states with potentially large outputs and documents the pass-by-reference pattern.
- Code Generation uses S3 URIs or DynamoDB references for payloads expected to exceed 32 KB (conservative threshold to allow headroom).
- Build and Test includes a payload-size assertion for states with variable-size outputs.

---

## Rule ORCH-11: Orchestrator IAM Roles Follow Least Privilege

**Severity**: P0 — Blocking

**Rule**: The IAM execution role for MWAA (the environment role) and for each Step Functions State Machine MUST follow least privilege. MWAA environment roles MUST NOT use `arn:aws:iam::aws:policy/AdministratorAccess` or wildcard resource policies. Step Functions roles MUST scope permissions to the specific Lambda functions, Glue jobs, EMR clusters, or ECS tasks the State Machine invokes — not to `*` resources. Service-level wildcards (e.g., `glue:*` on `*`) are prohibited unless justified and time-bounded.

**Verification**:
- Infrastructure Design lists the IAM role per orchestrator with a permission inventory: action, resource ARN or ARN pattern, and justification.
- No wildcard resource (`"Resource": "*"`) in production roles except where AWS requires it (e.g., `cloudwatch:PutMetricData`).
- MWAA environment role is scoped to the specific S3 bucket, KMS key, Secrets Manager paths, and CloudWatch log groups for the environment.
- Build and Test includes an IAM policy linting step (e.g., AWS IAM Access Analyzer, cfn-python-lint, or Checkov).

---

## Rule ORCH-12: Schedule and Trigger Source Documented Per Pipeline

**Severity**: P1 — Warning

**Rule**: Every pipeline MUST have a documented trigger source: cron schedule, event-driven trigger (S3 event → EventBridge → MWAA REST API or Step Functions, SNS, SQS), or manual-only. Pipelines triggered by multiple sources MUST document concurrency behavior — what happens if a scheduled run and an event-driven run overlap. "Triggered ad hoc" is not an acceptable production trigger definition.

**Verification**:
- Requirements Analysis or Functional Design lists the trigger source per pipeline with timing guarantees.
- Event-driven pipelines document the EventBridge rule or S3 notification configuration.
- Pipelines with multiple trigger sources document `max_active_runs` (MWAA) or a concurrency-check state (Step Functions) that prevents overlapping executions from conflicting.

---

## Rule ORCH-13: Pipeline Observability — SLA Miss Alerting Required

**Severity**: P1 — Warning

**Rule**: Every production pipeline MUST have an SLA miss alert: a notification fires if the pipeline has not completed successfully by its SLA deadline. For MWAA, this is an SLA callback or an external CloudWatch alarm on `TaskInstanceDuration` / `DagRunDurationSuccess`. For Step Functions, this is a CloudWatch alarm on `ExecutionsFailed` or `ExecutionTime` exceeding a threshold, routed to SNS. Dashboards without alerting are not sufficient. This composes with DATAENG-09 and DATAENG-12.

**Verification**:
- Infrastructure Design provisions an SLA alarm per production pipeline.
- MWAA DAGs with consumer-facing SLAs use Airflow SLA callbacks or equivalent external alerting.
- Step Functions pipelines have CloudWatch alarms on `ExecutionsFailed` and `ExecutionThrottled` routed to SNS.
- Build and Test includes a synthetic SLA breach that triggers and resolves the alarm.

---

## Rule ORCH-14: DAG and State Machine Versioning and Promotion Process

**Severity**: P1 — Warning

**Rule**: DAG files and State Machine definitions MUST be version-controlled and deployed via a CI/CD pipeline — no manual edits to DAG files in the S3 DAGs bucket or direct updates to State Machine definitions in the console in production. Deployments MUST be gated on DAG import tests (MWAA) or ASL schema validation (Step Functions). Environment promotion (dev → staging → prod) MUST follow the same process as application code.

**Verification**:
- Infrastructure Design names the CI/CD mechanism (CodePipeline, GitHub Actions, etc.) and the promotion gates.
- Build and Test documents the DAG import test (MWAA) or the ASL validation step (Step Functions).
- Production DAGs bucket has an S3 bucket policy or IAM boundary that prevents direct human writes outside the CI/CD role.
- State Machine ARNs in production are tagged with the deploying pipeline and commit SHA.

---

## Rule ORCH-15: Dead-Letter and Failed-Execution Retention Policy

**Severity**: P1 — Warning

**Rule**: Failed MWAA DAG runs and failed Step Functions executions MUST be retained long enough to diagnose and replay. For MWAA: task logs in CloudWatch MUST have a retention policy of at least 30 days. For Step Functions Standard Workflows: execution history is retained for 90 days by AWS; Express Workflow logs in CloudWatch MUST have a retention policy set (default = never expire, which incurs unbounded cost). Failed executions MUST route to an alert or dead-letter mechanism so they are not silently abandoned.

**Verification**:
- Infrastructure Design sets CloudWatch log group retention for MWAA task logs (minimum 30 days).
- Express Workflow log groups have explicit retention set (30–90 days recommended; cost model justifies longer).
- Failed Step Functions executions trigger an SNS notification or EventBridge rule per ORCH-08.
- Failed MWAA DAG runs trigger an on-failure callback or alerting DAG.
