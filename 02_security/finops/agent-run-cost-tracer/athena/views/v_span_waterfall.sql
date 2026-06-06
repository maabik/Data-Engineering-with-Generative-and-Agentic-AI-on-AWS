-- Span waterfall for a single run — shows the sequence of sub-calls in time order.
-- Pass ?run_id=<value> as a parameter in Athena named queries.
-- Used for debugging expensive runs: which sub-call drove the cost?

CREATE OR REPLACE VIEW ai_finops.v_span_waterfall AS
SELECT
    s.run_id,
    s.span_id,
    s.span_type,
    s.started_at,
    s.ended_at,
    s.duration_ms,
    s.model_id,
    s.input_tokens,
    s.output_tokens,
    s.estimated_cost_usd,
    s.tool_name,
    s.tool_success,
    s.kb_id,
    s.retrieved_chunks,
    s.embedding_model,
    s.embedding_input_tokens,
    s.error,
    -- Cumulative cost up to this span (waterfall total)
    SUM(s.estimated_cost_usd) OVER (
        PARTITION BY s.run_id
        ORDER BY s.started_at
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                                   AS cumulative_cost_usd,
    -- % of run total cost
    ROUND(
        100.0 * s.estimated_cost_usd
        / NULLIF(r.total_estimated_cost_usd, 0), 2
    )                                   AS pct_of_run_cost
FROM ai_finops.agent_spans s
JOIN ai_finops.agent_runs r USING (run_id)
ORDER BY s.run_id, s.started_at;
