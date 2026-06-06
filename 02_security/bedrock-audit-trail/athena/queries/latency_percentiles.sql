-- Latency P50/P95/P99 by model and day.

SELECT
    date,
    model_id,
    COUNT(*)                                    AS invocation_count,
    ROUND(AVG(latency_ms), 0)                   AS avg_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.5)          AS p50_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.95)         AS p95_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.99)         AS p99_latency_ms,
    MAX(latency_ms)                             AS max_latency_ms,
    -- Latency per output token (model efficiency proxy)
    ROUND(
        AVG(CAST(latency_ms AS DOUBLE) / NULLIF(output_tokens, 0)),
        2
    )                                           AS avg_ms_per_output_token
FROM bedrock_audit.invocations
WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
  AND latency_ms > 0
GROUP BY date, model_id
ORDER BY date DESC, p99_latency_ms DESC;
