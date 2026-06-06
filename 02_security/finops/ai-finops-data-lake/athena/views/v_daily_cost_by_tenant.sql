-- Daily cost breakdown by tenant and service category.
-- Primary view for the QuickSight cost cockpit top-line panel.

CREATE OR REPLACE VIEW ai_finops.v_daily_cost_by_tenant AS
SELECT
    date,
    tenant_id,
    service_category,
    model_family,
    workload,
    account_id,
    region,
    COUNT(*)                                                    AS request_count,
    SUM(input_tokens)                                           AS total_input_tokens,
    SUM(output_tokens)                                          AS total_output_tokens,
    SUM(total_tokens)                                           AS total_tokens,
    SUM(COALESCE(actual_cost_usd, estimated_cost_usd))          AS total_cost_usd,
    SUM(estimated_cost_usd)                                     AS total_estimated_cost_usd,
    SUM(actual_cost_usd)                                        AS total_actual_cost_usd,
    ROUND(AVG(latency_ms) FILTER (WHERE latency_ms > 0), 0)     AS avg_latency_ms,
    COUNT(*) FILTER (WHERE pii_detected)                        AS pii_events,
    COUNT(*) FILTER (WHERE injection_detected)                  AS injection_events,
    COUNT(*) FILTER (WHERE guardrail_action = 'BLOCKED')        AS guardrail_blocks,
    COUNT(*) FILTER (WHERE error_code <> '' AND error_code IS NOT NULL) AS error_count
FROM ai_finops.invocations
WHERE source IN ('bedrock_invocation', 'agentcore_runtime', 'cur', 'cur_bedrock')
GROUP BY date, tenant_id, service_category, model_family, workload, account_id, region;
