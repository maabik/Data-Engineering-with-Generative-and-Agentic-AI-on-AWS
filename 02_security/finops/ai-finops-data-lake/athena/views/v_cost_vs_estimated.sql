-- Reconciliation view: compare on-demand price-table estimates vs. actual CUR costs.
-- Highlights pricing delta for provisioned throughput, savings plans, credits.

CREATE OR REPLACE VIEW ai_finops.v_cost_vs_estimated AS
SELECT
    date,
    account_id,
    model_family,
    tenant_id,
    COUNT(*)                                                    AS invocation_count,
    SUM(estimated_cost_usd)                                     AS sum_estimated_usd,
    SUM(actual_cost_usd)                                        AS sum_actual_usd,
    ROUND(
        100.0 * (SUM(actual_cost_usd) - SUM(estimated_cost_usd))
        / NULLIF(SUM(estimated_cost_usd), 0), 2
    )                                                           AS variance_pct,
    COUNT(*) FILTER (WHERE joined = true)                       AS joined_count,
    COUNT(*) FILTER (WHERE joined = false)                      AS unjoined_count
FROM ai_finops.invocations
WHERE source = 'bedrock_invocation'
  AND actual_cost_usd IS NOT NULL
GROUP BY date, account_id, model_family, tenant_id;
