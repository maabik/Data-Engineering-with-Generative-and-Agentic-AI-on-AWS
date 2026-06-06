-- SAME-SHARD-001 UPSERT Test - Insert and Update History
-- Both operations processed by the same Kinesis shard for consistency

SELECT 
    'CURRENT' as version,
    trade_id,
    stock_symbol,
    price,
    quantity,
    trade_type,
    timestamp,
    'Updated version - TSLA $275.50 x 250 SELL' as description
FROM trading.trades 
WHERE trade_id = 'SAME-SHARD-001'

UNION ALL

SELECT 
    'ORIGINAL' as version,
    trade_id,
    stock_symbol,
    price,
    quantity,
    trade_type,
    timestamp,
    'Original version - TSLA $250.00 x 200 BUY' as description
FROM trading.trades FOR TIMESTAMP AS OF (current_timestamp - interval '3' minute)
WHERE trade_id = 'SAME-SHARD-001'

ORDER BY version DESC;

-- Expected Results:
-- Row 1: CURRENT  - SAME-SHARD-001, TSLA, 275.50, 250, SELL (final state)
-- Row 2: ORIGINAL - SAME-SHARD-001, TSLA, 250.00, 200, BUY  (initial state)
