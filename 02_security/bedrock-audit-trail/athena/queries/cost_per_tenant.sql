-- Cost per tenant per day.
-- Token counts map to pricing tiers; adjust the per-token rates for your models.
-- Uses approximate on-demand pricing for claude-3-sonnet as a reference.

SELECT
    date,
    tenant_id,
    model_id,
    COUNT(*)                                    AS invocation_count,
    SUM(input_tokens)                           AS total_input_tokens,
    SUM(output_tokens)                          AS total_output_tokens,
    -- Approximate cost in USD (claude-3-sonnet pricing as example)
    ROUND(SUM(input_tokens)  / 1e6 * 3.00, 4)  AS approx_input_cost_usd,
    ROUND(SUM(output_tokens) / 1e6 * 15.00, 4) AS approx_output_cost_usd,
    ROUND(
        SUM(input_tokens)  / 1e6 * 3.00 +
        SUM(output_tokens) / 1e6 * 15.00, 4
    )                                           AS approx_total_cost_usd
FROM bedrock_audit.invocations
WHERE date >= CURRENT_DATE - INTERVAL '30' DAY
  AND sampled = true
GROUP BY date, tenant_id, model_id
ORDER BY date DESC, approx_total_cost_usd DESC;
