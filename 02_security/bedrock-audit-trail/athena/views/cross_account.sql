-- Cross-account aggregation view.
-- In the central account, create the Glue table pointing to the replication bucket,
-- then create this view to query across all source accounts uniformly.
-- The account_id column distinguishes origin accounts.

CREATE OR REPLACE VIEW bedrock_audit_central.v_all_accounts AS
SELECT
    account_id,
    region,
    date,
    model_family,
    tenant_id,
    COUNT(*)                                AS invocation_count,
    SUM(input_tokens)                       AS total_input_tokens,
    SUM(output_tokens)                      AS total_output_tokens,
    SUM(input_tokens + output_tokens)       AS total_tokens,
    APPROX_PERCENTILE(latency_ms, 0.5)      AS p50_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.95)     AS p95_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.99)     AS p99_latency_ms,
    COUNT(*) FILTER (WHERE pii_detected)    AS pii_events,
    COUNT(*) FILTER (WHERE injection_detected) AS injection_events,
    COUNT(*) FILTER (WHERE guardrail_action = 'BLOCKED') AS guardrail_blocks,
    COUNT(*) FILTER (WHERE error_code <> '') AS error_count
FROM bedrock_audit_central.invocations_all_accounts
GROUP BY account_id, region, date, model_family, tenant_id;
