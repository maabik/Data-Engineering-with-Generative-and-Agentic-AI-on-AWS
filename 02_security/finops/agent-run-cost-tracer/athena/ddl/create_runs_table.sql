-- Stitched run records table (Iceberg).
-- One row per user turn. This is the table everything else queries.

CREATE TABLE IF NOT EXISTS ai_finops.agent_runs (
    run_id                      VARCHAR,
    trace_id                    VARCHAR,
    user_id                     VARCHAR,
    tenant_id                   VARCHAR,
    session_id                  VARCHAR,
    workload                    VARCHAR,
    started_at                  TIMESTAMP,
    ended_at                    TIMESTAMP,
    wall_clock_ms               INT,
    total_spans                 INT,
    model_invocations           INT,
    tool_invocations            INT,
    retrieval_calls             INT,
    embedding_calls             INT,
    tool_errors                 INT,
    span_errors                 INT,
    total_input_tokens          INT,
    total_output_tokens         INT,
    total_tokens                INT,
    total_embedding_tokens      INT,
    total_estimated_cost_usd    DOUBLE,
    orchestration_cost_usd      DOUBLE  COMMENT 'First model call = orchestrator',
    retrieval_cost_usd          DOUBLE  COMMENT 'Embedding API cost for retrieval',
    tool_cost_usd               DOUBLE,
    cost_amplification          DOUBLE  COMMENT 'total_cost / cheapest_single_model_call',
    total_retrieved_chunks      INT,
    model_breakdown             VARCHAR COMMENT 'JSON: {model_id: {invocations,tokens,cost}}'
)
PARTITIONED BY (date DATE)
LOCATION 's3://${BUCKET}/agent-runs/'
TBLPROPERTIES (
    'table_type' = 'ICEBERG',
    'format'     = 'PARQUET',
    'write_compression' = 'SNAPPY'
);
