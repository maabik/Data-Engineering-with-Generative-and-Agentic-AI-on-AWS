-- Total cost per team per day, last 30 days, ordered by spend
SELECT
    usage_date,
    dimension_value AS team,
    service,
    ROUND(SUM(amount), 2) AS amount_usd
FROM pipeline_cost_attribution.costs
WHERE dimension = 'team'
  AND usage_date >= date_format(current_date - interval '30' day, '%Y-%m-%d')
GROUP BY usage_date, dimension_value, service
ORDER BY amount_usd DESC
LIMIT 200;
