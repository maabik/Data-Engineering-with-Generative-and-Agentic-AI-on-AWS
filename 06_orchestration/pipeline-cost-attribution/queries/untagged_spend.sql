-- Surface spend that is escaping the chargeback model
SELECT
    dimension,
    service,
    ROUND(SUM(amount), 2) AS amount_usd
FROM pipeline_cost_attribution.costs
WHERE dimension_value = 'untagged'
  AND usage_date >= date_format(current_date - interval '30' day, '%Y-%m-%d')
GROUP BY dimension, service
ORDER BY amount_usd DESC
LIMIT 100;
