-- Common analytical queries against trading.trades.
-- Used by the hourly market-summary Lambda and as classroom examples.

-- 1. Per-symbol price + volume in the last hour.
SELECT
    stock_symbol,
    COUNT(*)              AS trade_count,
    SUM(quantity)         AS total_quantity,
    ROUND(AVG(price), 2)  AS avg_price,
    ROUND(MIN(price), 2)  AS min_price,
    ROUND(MAX(price), 2)  AS max_price,
    ROUND(SUM(total_value), 2) AS notional_volume
FROM trading.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY notional_volume DESC;

-- 2. Buy/sell pressure by symbol (last hour).
SELECT
    stock_symbol,
    SUM(CASE WHEN trade_type = 'BUY'  THEN 1 ELSE 0 END) AS buys,
    SUM(CASE WHEN trade_type = 'SELL' THEN 1 ELSE 0 END) AS sells,
    ROUND(
        100.0 * SUM(CASE WHEN trade_type = 'BUY' THEN 1 ELSE 0 END) /
        NULLIF(COUNT(*), 0),
        1
    ) AS buy_pct
FROM trading.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY buy_pct DESC;

-- 3. Top movers — biggest delta between min and max price in the window.
SELECT
    stock_symbol,
    ROUND(MIN(price), 2) AS low,
    ROUND(MAX(price), 2) AS high,
    ROUND(MAX(price) - MIN(price), 2) AS spread,
    ROUND(100.0 * (MAX(price) - MIN(price)) / NULLIF(MIN(price), 0), 2) AS pct_range
FROM trading.trades
WHERE "timestamp" >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY pct_range DESC
LIMIT 10;

-- 4. Hourly throughput — useful for capacity planning.
SELECT
    date_trunc('hour', "timestamp") AS hour,
    COUNT(*) AS trades_per_hour
FROM trading.trades
WHERE "timestamp" >= current_timestamp - interval '24' hour
GROUP BY 1
ORDER BY 1 DESC;
