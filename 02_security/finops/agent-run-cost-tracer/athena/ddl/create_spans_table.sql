-- Raw span events table (Iceberg).
-- Partitioned by date so the stitcher can query one day efficiently.
-- Replace ${BUCKET} with the CDK stack output.

CREATE TABLE IF NOT EXISTS ai_finops.agent_spans (
    span_id                 VARCHAR,
    trace_id                VARCHAR,
    run_id                  VARCHAR,
    span_type               VARCHAR     COMMENT 'model | tool | retrieval | embedding | agent | sub_agent',
    started_at              TIMESTAMP,
    ended_at                TIMESTAMP,
    duration_ms             INT,
    model_id                VARCHAR,
    input_tokens            INT,
    output_tokens           INT,
    total_tokens            INT,
    estimated_cost_usd      DOUBLE,
    tool_name               VARCHAR,
    tool_success            BOOLEAN,
    kb_id                   VARCHAR,
    retrieval_query         VARCHAR,
    retrieved_chunks        INT,
    embedding_model         VARCHAR,
    embedding_input_tokens  INT,
    agent_id                VARCHAR,
    agent_name              VARCHAR,
    user_id                 VARCHAR,
    tenant_id               VARCHAR,
    session_id              VARCHAR,
    workload                VARCHAR,
    error                   VARCHAR
)
PARTITIONED BY (date DATE)
LOCATION 's3://${BUCKET}/agent-spans/'
TBLPROPERTIES (
    'table_type' = 'ICEBERG',
    'format'     = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
