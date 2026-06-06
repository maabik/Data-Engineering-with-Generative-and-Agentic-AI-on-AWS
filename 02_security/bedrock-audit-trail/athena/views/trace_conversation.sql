-- View: stitches multi-step agent traces into a single conversation record.
-- Each row = one conversation (trace_id), with all invocations aggregated.

CREATE OR REPLACE VIEW bedrock_audit.v_trace_conversations AS
SELECT
    trace_id,
    MIN(event_time)                                     AS conversation_start,
    MAX(event_time)                                     AS conversation_end,
    date_diff('millisecond',
              MIN(event_time),
              MAX(event_time))                          AS total_duration_ms,
    COUNT(*)                                            AS turn_count,
    ARBITRARY(user_id)                                  AS user_id,
    ARBITRARY(tenant_id)                                AS tenant_id,
    ARBITRARY(session_id)                               AS session_id,
    ARRAY_AGG(model_id ORDER BY event_time)             AS models_used,
    SUM(input_tokens)                                   AS total_input_tokens,
    SUM(output_tokens)                                  AS total_output_tokens,
    SUM(input_tokens + output_tokens)                   AS total_tokens,
    ROUND(AVG(latency_ms), 0)                           AS avg_turn_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.5)                  AS p50_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.95)                 AS p95_latency_ms,
    APPROX_PERCENTILE(latency_ms, 0.99)                 AS p99_latency_ms,
    BOOL_OR(pii_detected)                               AS any_pii_detected,
    BOOL_OR(injection_detected)                         AS any_injection_detected,
    ARRAY_AGG(DISTINCT guardrail_action) FILTER (
        WHERE guardrail_action <> 'NONE')               AS guardrail_actions,
    COUNT(*) FILTER (WHERE error_code <> '')            AS error_count,
    ARRAY_AGG(request_id ORDER BY event_time)           AS request_ids
FROM bedrock_audit.invocations
WHERE trace_id IS NOT NULL AND trace_id <> ''
GROUP BY trace_id;
