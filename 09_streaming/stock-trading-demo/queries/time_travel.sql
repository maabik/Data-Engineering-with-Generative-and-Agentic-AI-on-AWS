-- Apache Iceberg time-travel queries against the trading.trades table.
-- Iceberg tracks every commit as a snapshot, so you can query the table as it
-- existed at any point in time or at a specific snapshot id.
--
-- Run from Athena (engine v3) — it must have access to the Glue catalog and
-- the s3tables-managed bucket via Lake Formation.

-- 1. Current state.
SELECT trade_id, stock_symbol, price, quantity, trade_type, "timestamp"
FROM trading.trades
WHERE trade_id = 'SAME-SHARD-001';

-- 2. Same row, ten minutes ago.
SELECT trade_id, stock_symbol, price, quantity, trade_type, "timestamp"
FROM trading.trades FOR TIMESTAMP AS OF (current_timestamp - interval '10' minute)
WHERE trade_id = 'SAME-SHARD-001';

-- 3. List all snapshots so you can pick a specific commit by id.
SELECT committed_at, snapshot_id, parent_id, operation, summary
FROM "trading"."trades$snapshots"
ORDER BY committed_at DESC
LIMIT 20;

-- 4. Query at a specific snapshot id (replace <snapshot_id> with a value from #3).
-- SELECT * FROM trading.trades FOR VERSION AS OF <snapshot_id>
-- WHERE trade_id = 'SAME-SHARD-001';

-- 5. See what changed in the last commit — diff between the two most recent snapshots.
WITH latest_snapshots AS (
    SELECT snapshot_id, parent_id
    FROM "trading"."trades$snapshots"
    ORDER BY committed_at DESC
    LIMIT 1
)
SELECT 'CURRENT'  AS version, t.*
FROM trading.trades FOR VERSION AS OF (SELECT snapshot_id FROM latest_snapshots) AS t
WHERE trade_id = 'SAME-SHARD-001'
UNION ALL
SELECT 'PREVIOUS' AS version, t.*
FROM trading.trades FOR VERSION AS OF (SELECT parent_id FROM latest_snapshots) AS t
WHERE trade_id = 'SAME-SHARD-001';
