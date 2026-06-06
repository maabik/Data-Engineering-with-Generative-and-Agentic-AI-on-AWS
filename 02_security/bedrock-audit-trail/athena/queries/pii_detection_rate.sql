-- PII detection rate by model and day.

SELECT
    date,
    model_family,
    COUNT(*)                                            AS total_invocations,
    COUNT(*) FILTER (WHERE pii_detected)                AS pii_invocations,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE pii_detected)
        / NULLIF(COUNT(*), 0), 2
    )                                                   AS pii_rate_pct,
    -- Break down by entity type (unnested)
    ARRAY_AGG(DISTINCT entity_type) FILTER (WHERE entity_type IS NOT NULL) AS entity_types_seen
FROM bedrock_audit.invocations
CROSS JOIN UNNEST(pii_entity_types) AS t(entity_type)
WHERE date >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY date, model_family
ORDER BY date DESC, pii_rate_pct DESC;
