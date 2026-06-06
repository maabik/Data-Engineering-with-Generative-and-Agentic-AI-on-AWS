-- The primary FinOps view: surfaces the hidden cost multiplier for every run.
-- Sort by cost_amplification DESC to find the most expensive user questions.

CREATE OR REPLACE VIEW ai_finops.v_run_cost_breakdown AS
SELECT
    date,
    run_id,
    tenant_id,
    user_id,
    workload,
    wall_clock_ms,

    -- Invocation counts (the hidden complexity)
    model_invocations,
    tool_invocations,
    retrieval_calls,
    embedding_calls,

    -- Token totals
    total_input_tokens,
    total_output_tokens,
    total_tokens,
    total_embedding_tokens,

    -- Cost breakdown
    total_estimated_cost_usd,
    orchestration_cost_usd,
    retrieval_cost_usd,
    ROUND(total_estimated_cost_usd - orchestration_cost_usd - retrieval_cost_usd, 8)
                                        AS other_cost_usd,

    -- The headline number: how many × more expensive than a naive single-call estimate
    cost_amplification,

    -- Quality signals
    total_retrieved_chunks,
    tool_errors,
    span_errors,

    -- Cost per output token (efficiency proxy)
    ROUND(
        total_estimated_cost_usd / NULLIF(total_output_tokens, 0) * 1000,
        6
    )                                   AS cost_per_1k_output_tokens
FROM ai_finops.agent_runs
WHERE total_estimated_cost_usd > 0;
