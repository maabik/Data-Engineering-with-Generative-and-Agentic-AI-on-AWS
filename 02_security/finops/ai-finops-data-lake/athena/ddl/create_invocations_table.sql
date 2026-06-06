-- AI FinOps Iceberg table — the substrate all FinOps projects query.
-- Replace ${ICEBERG_BUCKET} with the CDK stack output: IcebergBucketName.
-- This table unifies all five sources:
--   (a) bedrock_invocation  — Bedrock model invocation logs
--   (b) cur                 — CUR 2.0 line items (Bedrock, OpenSearch, S3)
--   (c) agentcore_runtime   — AgentCore agent execution summaries
--   (d) ce_*                — Cost Explorer (when CUR is not configured)
--   (e) adjacent service categories: bedrock_kb, opensearch, s3_vectors, bedrock_guardrail

CREATE TABLE IF NOT EXISTS ai_finops.invocations (

    -- Identity & provenance
    event_time              TIMESTAMP       COMMENT 'UTC time of the invocation or CUR period start',
    source                  VARCHAR         COMMENT 'bedrock_invocation | cur | agentcore_runtime | cur_opensearch | ce_* ...',
    account_id              VARCHAR,
    region                  VARCHAR,
    request_id              VARCHAR         COMMENT 'Bedrock requestId or CUR resource_id or AgentCore executionId',
    cur_line_item_id        VARCHAR         COMMENT 'CUR identity_line_item_id (populated after join)',
    iam_principal           VARCHAR         COMMENT 'line_item_iam_principal from CUR',

    -- Model / routing
    model_id                VARCHAR         COMMENT 'e.g. anthropic.claude-3-sonnet-20240229-v1:0',
    model_family            VARCHAR         COMMENT 'anthropic.claude-3-sonnet-20240229-v1 (no version suffix)',
    inference_profile_id    VARCHAR,
    inference_profile_name  VARCHAR,

    -- Attribution
    tenant_id               VARCHAR         COMMENT 'From requestMetadata or cost-allocation tags',
    user_id                 VARCHAR,
    session_id              VARCHAR,
    trace_id                VARCHAR         COMMENT 'Spans multi-step agent runs; join key for conversation view',
    workload                VARCHAR         COMMENT 'Application name / workload tag',
    team                    VARCHAR,
    environment             VARCHAR,

    -- Usage metrics
    input_tokens            INT,
    output_tokens           INT,
    total_tokens            INT,
    latency_ms              INT,
    usage_amount            DOUBLE          COMMENT 'Raw CUR usage_amount (token count, request count, etc.)',
    usage_type              VARCHAR         COMMENT 'CUR usage_type string',

    -- Cost
    estimated_cost_usd      DOUBLE          COMMENT 'Computed from token counts × pricing table',
    actual_cost_usd         DOUBLE          COMMENT 'From CUR (null when not joined)',
    product_code            VARCHAR         COMMENT 'AWS product code (AmazonBedrock, AmazonOpenSearchService, etc.)',
    service_category        VARCHAR         COMMENT 'bedrock | bedrock_kb | bedrock_guardrail | opensearch | s3_vectors | agentcore',

    -- Agent dimensions
    agent_id                VARCHAR,
    agent_name              VARCHAR,
    agent_alias_id          VARCHAR,
    execution_status        VARCHAR         COMMENT 'SUCCEEDED | FAILED | TIMED_OUT',
    model_invocations       INT             COMMENT 'LLM calls within one agent run',
    tool_invocations        INT,

    -- Security / quality signals
    guardrail_action        VARCHAR         COMMENT 'NONE | INTERVENED | BLOCKED',
    injection_detected      BOOLEAN,
    pii_detected            BOOLEAN,
    error_code              VARCHAR,

    -- Join metadata
    joined                  BOOLEAN         COMMENT 'true = CUR line item matched',
    join_method             VARCHAR         COMMENT 'exact_request_id | fuzzy_token | none'
)
PARTITIONED BY (date DATE, model_family VARCHAR, tenant_id VARCHAR)
LOCATION 's3://${ICEBERG_BUCKET}/finops/invocations/'
TBLPROPERTIES (
    'table_type'         = 'ICEBERG',
    'format'             = 'PARQUET',
    'write_compression'  = 'SNAPPY',
    'optimize_rewrite_delete_file_threshold' = '10'
);
