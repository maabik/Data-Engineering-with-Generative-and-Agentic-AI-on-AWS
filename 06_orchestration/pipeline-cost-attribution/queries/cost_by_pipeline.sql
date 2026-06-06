-- Per-pipeline running spend, broken out by service
SELECT
    dimension_value AS pipeline_id,
    service,
    ROUND(SUM(amount), 2) AS amount_usd,
    MIN(usage_date) AS first_seen,
    MAX(usage_date) AS last_seen
FROM pipeline_cost_attribution.costs
WHERE dimension = 'pipeline_id'
  AND usage_date >= date_format(current_date - interval '30' day, '%Y-%m-%d')
GROUP BY dimension_value, service
ORDER BY amount_usd DESC
LIMIT 500;
