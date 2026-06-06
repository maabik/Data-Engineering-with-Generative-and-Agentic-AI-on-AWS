-- Athena DDL for the Iceberg invocations table.
-- Replace ${ICEBERG_BUCKET} with the output from CDK stack: IcebergBucketName.
-- This matches the Glue catalog table created by the CDK stack;
-- run only if you need a standalone Athena-managed table instead.

CREATE TABLE IF NOT EXISTS bedrock_audit.invocations (
    event_time              TIMESTAMP,
    request_id              VARCHAR,
    account_id              VARCHAR,
    region                  VARCHAR,
    model_id                VARCHAR,
    user_id                 VARCHAR,
    tenant_id               VARCHAR,
    session_id              VARCHAR,
    trace_id                VARCHAR,
    input_tokens            INT,
    output_tokens           INT,
    latency_ms              INT,
    input_text_redacted     VARCHAR,
    output_text_redacted    VARCHAR,
    pii_detected            BOOLEAN,
    pii_entity_types        ARRAY(VARCHAR),
    pii_token_ids           ARRAY(VARCHAR),
    guardrail_action        VARCHAR,
    guardrail_id            VARCHAR,
    guardrail_topics_triggered  ARRAY(VARCHAR),
    guardrail_content_filters   ARRAY(VARCHAR),
    injection_detected      BOOLEAN,
    sampled                 BOOLEAN,
    error_code              VARCHAR,
    error_message           VARCHAR
)
PARTITIONED BY (date DATE, model_family VARCHAR, tenant_id_part VARCHAR)
LOCATION 's3://${ICEBERG_BUCKET}/data/'
TBLPROPERTIES (
    'table_type'         = 'ICEBERG',
    'format'             = 'PARQUET',
    'write_compression'  = 'SNAPPY',
    'optimize_rewrite_delete_file_threshold' = '10'
);
