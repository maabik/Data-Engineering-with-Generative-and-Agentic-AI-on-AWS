-- Chargeback view: cost rolled up per business_unit, last full month
WITH last_month AS (
    SELECT
        date_format(date_trunc('month', current_date) - interval '1' day, '%Y-%m-01') AS lm_start,
        date_format(date_trunc('month', current_date) - interval '1' day, '%Y-%m-%d') AS lm_end
)
SELECT
    dimension_value AS business_unit,
    ROUND(SUM(amount), 2) AS amount_usd,
    COUNT(DISTINCT usage_date) AS days_with_spend
FROM pipeline_cost_attribution.costs, last_month
WHERE dimension = 'business_unit'
  AND usage_date BETWEEN lm_start AND lm_end
GROUP BY dimension_value
ORDER BY amount_usd DESC;
