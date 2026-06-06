-- Model spend share: how much each model costs relative to total, per period.
-- Feeds the "model mix" panel in the cost cockpit.

CREATE OR REPLACE VIEW ai_finops.v_model_spend_share AS
WITH totals AS (
    SELECT
        date,
        tenant_id,
        SUM(COALESCE(actual_cost_usd, estimated_cost_usd)) AS day_total_cost
    FROM ai_finops.invocations
    WHERE source IN ('bedrock_invocation', 'agentcore_runtime')
    GROUP BY date, tenant_id
)
SELECT
    i.date,
    i.tenant_id,
    i.model_id,
    i.model_family,
    COUNT(*)                                                    AS invocation_count,
    SUM(i.total_tokens)                                         AS total_tokens,
    SUM(COALESCE(i.actual_cost_usd, i.estimated_cost_usd))      AS model_cost_usd,
    t.day_total_cost,
    ROUND(
        100.0 * SUM(COALESCE(i.actual_cost_usd, i.estimated_cost_usd))
        / NULLIF(t.day_total_cost, 0), 2
    )                                                           AS spend_share_pct
FROM ai_finops.invocations i
JOIN totals t USING (date, tenant_id)
WHERE i.source IN ('bedrock_invocation', 'agentcore_runtime')
GROUP BY i.date, i.tenant_id, i.model_id, i.model_family, t.day_total_cost;
