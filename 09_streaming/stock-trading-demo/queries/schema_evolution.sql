-- Apache Iceberg schema evolution: add, rename, reorder, and drop columns
-- without rewriting the table or breaking existing queries.
--
-- Iceberg uses field IDs internally, so renames don't break old snapshots and
-- adds default to NULL for historical rows. Run statements one at a time.

-- 1. Add a new column for fee tracking.
ALTER TABLE trading.trades
ADD COLUMNS (commission_usd double);

-- 2. Add a column with a comment.
ALTER TABLE trading.trades
ADD COLUMNS (settlement_date date COMMENT 'T+2 settlement date');

-- 3. Rename a column (existing data is preserved, queries against the old
--    name will fail — Athena rewrites the schema, not the data files).
-- ALTER TABLE trading.trades RENAME COLUMN volatility TO volatility_index;

-- 4. Widen a numeric type (compatible change).
-- ALTER TABLE trading.trades ALTER COLUMN quantity TYPE bigint;

-- 5. Drop a column.
-- ALTER TABLE trading.trades DROP COLUMN commission_usd;

-- 6. Inspect the schema history to confirm changes were applied.
SELECT *
FROM "trading"."trades$history"
ORDER BY made_current_at DESC
LIMIT 20;

-- 7. Read the current schema with column ids — useful for confirming that a
--    rename kept the underlying field id stable.
DESCRIBE EXTENDED trading.trades;
