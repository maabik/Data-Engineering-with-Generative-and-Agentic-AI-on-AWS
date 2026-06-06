-- Guardrail trigger rates: blocks, interventions, injection detections.

SELECT
    date,
    guardrail_id,
    COUNT(*)                                                    AS total_invocations,
    COUNT(*) FILTER (WHERE guardrail_action = 'BLOCKED')        AS blocked_count,
    COUNT(*) FILTER (WHERE guardrail_action = 'INTERVENED')     AS intervened_count,
    COUNT(*) FILTER (WHERE injection_detected)                  AS injection_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE guardrail_action = 'BLOCKED')
        / NULLIF(COUNT(*), 0), 2)                               AS block_rate_pct,
    ROUND(100.0 * COUNT(*) FILTER (WHERE injection_detected)
        / NULLIF(COUNT(*), 0), 2)                               AS injection_rate_pct,
    -- Most commonly triggered topics
    ARRAY_AGG(topic ORDER BY topic) FILTER (WHERE topic IS NOT NULL) AS topics_triggered
FROM bedrock_audit.invocations
CROSS JOIN UNNEST(guardrail_topics_triggered) AS t(topic)
WHERE date >= CURRENT_DATE - INTERVAL '30' DAY
  AND guardrail_id <> ''
GROUP BY date, guardrail_id
ORDER BY date DESC, block_rate_pct DESC;
