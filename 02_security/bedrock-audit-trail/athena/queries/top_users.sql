-- Top users by token consumption in the last 7 days.

SELECT
    user_id,
    tenant_id,
    COUNT(*)                            AS invocation_count,
    SUM(input_tokens + output_tokens)   AS total_tokens,
    SUM(input_tokens)                   AS total_input_tokens,
    SUM(output_tokens)                  AS total_output_tokens,
    APPROX_PERCENTILE(latency_ms, 0.5)  AS p50_latency_ms,
    COUNT(*) FILTER (WHERE pii_detected)        AS pii_events,
    COUNT(*) FILTER (WHERE injection_detected)  AS injection_events,
    ARRAY_AGG(DISTINCT model_id)        AS models_used
FROM bedrock_audit.invocations
WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
  AND user_id <> ''
GROUP BY user_id, tenant_id
ORDER BY total_tokens DESC
LIMIT 50;
