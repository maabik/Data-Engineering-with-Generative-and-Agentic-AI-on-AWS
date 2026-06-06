-- Top runs by cost and amplification — the "expensive questions" report.
-- Feeds the FinOps alert: any run with amplification > threshold gets flagged.

CREATE OR REPLACE VIEW ai_finops.v_expensive_runs AS
SELECT
    date,
    tenant_id,
    user_id,
    workload,
    run_id,
    total_estimated_cost_usd,
    cost_amplification,
    model_invocations,
    tool_invocations,
    retrieval_calls,
    embedding_calls,
    total_tokens,
    wall_clock_ms,
    CASE
        WHEN cost_amplification >= 20   THEN 'critical'
        WHEN cost_amplification >= 10   THEN 'high'
        WHEN cost_amplification >= 5    THEN 'medium'
        ELSE 'low'
    END                                 AS amplification_tier
FROM ai_finops.v_run_cost_breakdown
WHERE date >= CURRENT_DATE - INTERVAL '7' DAY
ORDER BY total_estimated_cost_usd DESC
LIMIT 500;
