"""Mutation catalog for schema-evolution-simulator.

Each mutation is a dict describing one schema change: its category, the DDL to
run, a validation query, and the expected behaviour across Athena, Redshift
Spectrum, and Glue ETL.

Categories
----------
safe      Iceberg spec guarantees backward compatibility — old data still readable.
unsafe    Iceberg spec allows it but data integrity or consumer compatibility is at risk.
chaos     Deliberately breaks something to observe how consumers react.

Consumer behaviour keys
-----------------------
athena            Expected outcome when a SELECT is run against the table.
redshift_spectrum What Redshift Spectrum typically does (informational — not tested live).
glue_etl          What a Glue DynamicFrame read would produce.
"""

from __future__ import annotations

# Each mutation dict keys:
#   name            str  — unique slug
#   category        str  — safe | unsafe | chaos
#   title           str  — one-line summary for the report header
#   description     str  — longer explanation shown in --explain mode
#   ddl             str  — Athena SQL to execute (may be None for read-only probes)
#   validation_sql  str  — SELECT to run after DDL; must succeed for the test to pass
#   expect_ddl_ok   bool — True if the DDL itself should succeed
#   expect_rows     int | None — exact row count expected from validation_sql
#   iceberg_note    str  — why Iceberg does or does not support this
#   consumer_notes  dict — {athena, redshift_spectrum, glue_etl} behaviour strings

MUTATIONS: list[dict] = [
    # ── SAFE ──────────────────────────────────────────────────────────────────

    {
        "name": "add_nullable_column",
        "category": "safe",
        "title": "Add nullable column",
        "description": (
            "Adding a nullable column is the most common schema change. "
            "Iceberg assigns a new field ID; existing Parquet files are not rewritten. "
            "Reads of old files return NULL for the new column."
        ),
        "ddl": "ALTER TABLE {db}.{table} ADD COLUMNS (inspector STRING)",
        "validation_sql": (
            "SELECT permit_id, inspector FROM {db}.{table} "
            "WHERE inspector IS NULL LIMIT 5"
        ),
        "expect_ddl_ok": True,
        "expect_rows": 5,
        "iceberg_note": "Spec §3.5.1: adding an optional (nullable) field is always compatible.",
        "consumer_notes": {
            "athena": "Returns NULL for the new column on all existing rows. Safe.",
            "redshift_spectrum": "Returns NULL for new column. Safe — Spectrum honours Glue catalog.",
            "glue_etl": "DynamicFrame includes the column with null values. Safe.",
        },
    },
    {
        "name": "rename_column",
        "category": "safe",
        "title": "Rename column (fee_usd → permit_fee)",
        "description": (
            "Iceberg tracks columns by an internal field ID, not by name. "
            "Renaming a column is a metadata-only operation; Parquet files are untouched. "
            "Queries using the OLD name will fail after the rename, but the DATA is intact. "
            "Athena uses ALTER TABLE ... CHANGE COLUMN old_name new_name TYPE syntax."
        ),
        "ddl": "ALTER TABLE {db}.{table} CHANGE COLUMN fee_usd permit_fee DOUBLE",
        "validation_sql": "SELECT permit_id, permit_fee FROM {db}.{table} LIMIT 5",
        "expect_ddl_ok": True,
        "expect_rows": 5,
        "iceberg_note": "Field ID mapping means Parquet files need no rewrite on rename.",
        "consumer_notes": {
            "athena": "New name works immediately. Queries using 'fee_usd' fail with COLUMN_NOT_FOUND.",
            "redshift_spectrum": (
                "Spectrum re-reads the Glue catalog; new name visible after a short lag. "
                "Old-name queries fail."
            ),
            "glue_etl": "DynamicFrame resolves field by ID — new name appears without rewrite.",
        },
    },
    {
        "name": "add_column_with_default",
        "category": "unsafe",
        "title": "Add column with DEFAULT value (Athena limitation)",
        "description": (
            "Iceberg v2 supports column defaults, but Athena engine v3 rejects "
            "the DEFAULT keyword in ALTER TABLE ADD COLUMNS for Iceberg tables. "
            "The DDL fails at parse time. The safe workaround is to add a nullable column "
            "and backfill with UPDATE. This mutation is 'unsafe' because developers "
            "expecting Iceberg default support may be caught off-guard by the engine gap."
        ),
        "ddl": (
            "ALTER TABLE {db}.{table} ADD COLUMNS "
            "(processing_region STRING DEFAULT 'us-west-2')"
        ),
        "validation_sql": (
            "SELECT permit_id, processing_region FROM {db}.{table} LIMIT 5"
        ),
        "expect_ddl_ok": False,  # Athena rejects DEFAULT in ADD COLUMNS for Iceberg
        "expect_rows": None,
        "iceberg_note": (
            "Iceberg v2 supports column defaults, but Athena engine v3 does not "
            "implement DEFAULT in ALTER TABLE ADD COLUMNS."
        ),
        "consumer_notes": {
            "athena": "Rejects with InvalidRequestException — DEFAULT not supported in ADD COLUMNS.",
            "redshift_spectrum": (
                "May return NULL instead of the default — Spectrum does not always "
                "evaluate Iceberg defaults."
            ),
            "glue_etl": "DynamicFrame returns the actual stored value; default applied at write time.",
        },
    },
    {
        "name": "reorder_columns",
        "category": "safe",
        "title": "Reorder columns cosmetically",
        "description": (
            "Column order in Iceberg is positional in the schema spec but columns are "
            "read by field ID from Parquet. A reorder is a metadata-only change. "
            "SELECT * returns columns in the new schema order."
        ),
        # Athena doesn't support ALTER TABLE ... CHANGE COLUMN position directly;
        # we simulate by verifying SELECT * returns in declared schema order.
        "ddl": None,
        "validation_sql": (
            "SELECT * FROM {db}.{table} LIMIT 1"
        ),
        "expect_ddl_ok": True,
        "expect_rows": 1,
        "iceberg_note": "Column reordering is metadata-only; Parquet data unchanged.",
        "consumer_notes": {
            "athena": (
                "SELECT * returns columns in Glue catalog order. "
                "Named SELECTs unaffected."
            ),
            "redshift_spectrum": "Same as Athena — catalog order is authoritative.",
            "glue_etl": "DynamicFrame field order follows catalog schema.",
        },
    },
    {
        "name": "drop_column",
        "category": "safe",
        "title": "Drop column (address)",
        "description": (
            "Dropping a column is metadata-only in Iceberg. "
            "Old Parquet files retain the data physically, but the engine hides it. "
            "The field ID is retired — it will never be reused. "
            "This mutation drops 'address' from the baseline schema."
        ),
        "ddl": "ALTER TABLE {db}.{table} DROP COLUMN address",
        "validation_sql": (
            "SELECT permit_id, permit_type FROM {db}.{table} LIMIT 5"
        ),
        "expect_ddl_ok": True,
        "expect_rows": 5,
        "iceberg_note": (
            "Dropped field IDs are tombstoned. Physical data lingers until compaction."
        ),
        "consumer_notes": {
            "athena": "Column disappears from SELECT *. Explicit SELECT address fails.",
            "redshift_spectrum": "Column hidden after catalog refresh. Old files unchanged on S3.",
            "glue_etl": "DynamicFrame no longer exposes the field.",
        },
    },
    {
        "name": "widen_int_to_bigint",
        "category": "safe",
        "title": "Widen type: INT → BIGINT",
        "description": (
            "Type promotion from a narrower to a wider integer is safe in Iceberg. "
            "Iceberg specifies: INT→LONG, FLOAT→DOUBLE, DECIMAL precision increase. "
            "Old files are read with the widened type automatically."
        ),
        "ddl": "ALTER TABLE {db}.{table} CHANGE COLUMN record_count record_count BIGINT",
        "validation_sql": "SELECT permit_id, record_count FROM {db}.{table} LIMIT 5",
        "expect_ddl_ok": True,
        "expect_rows": 5,
        "iceberg_note": "INT→LONG is an allowed type promotion in Iceberg spec §3.11.",
        "consumer_notes": {
            "athena": "Values intact, type widened. No data rewrite needed.",
            "redshift_spectrum": "Spectrum casts on read; existing values preserved.",
            "glue_etl": "LongType used for the column in DynamicFrame.",
        },
    },

    # ── UNSAFE ────────────────────────────────────────────────────────────────

    {
        "name": "drop_referenced_column",
        "category": "unsafe",
        "title": "Drop column that a downstream query depends on (status)",
        "description": (
            "Iceberg allows the DROP, but any downstream view, saved query, "
            "or BI dashboard that references 'status' will break immediately. "
            "This is safe for the table format but dangerous operationally."
        ),
        "ddl": "ALTER TABLE {db}.{table} DROP COLUMN status",
        "validation_sql": "SELECT permit_id, status FROM {db}.{table} LIMIT 1",
        "expect_ddl_ok": True,
        "expect_rows": None,  # expect COLUMN_NOT_FOUND — this is the chaos
        "iceberg_note": "Iceberg allows drops; application code is the risk.",
        "consumer_notes": {
            "athena": (
                "SELECT status fails with COLUMN_NOT_FOUND after drop. "
                "SELECT * no longer includes it."
            ),
            "redshift_spectrum": "Same failure — column gone from external table definition.",
            "glue_etl": "DynamicFrame read succeeds but 'status' field absent. Transforms that use it fail.",
        },
    },
    {
        "name": "add_not_null_column",
        "category": "chaos",
        "title": "Add NOT NULL (required) column — Athena rejects it",
        "description": (
            "Iceberg allows required (NOT NULL) fields in theory, but Athena engine v3 "
            "rejects NOT NULL in ALTER TABLE ADD COLUMNS with an InvalidRequestException. "
            "This is a chaos mutation: developers expecting Iceberg's required-field support "
            "discover that Athena enforces a stricter parse. "
            "The correct workaround is to add the column as nullable and enforce the "
            "constraint at the application or DQ layer."
        ),
        "ddl": (
            "ALTER TABLE {db}.{table} ADD COLUMNS (auditor_id STRING NOT NULL)"
        ),
        "validation_sql": "SELECT permit_id FROM {db}.{table} LIMIT 5",
        "expect_ddl_ok": False,  # Athena rejects NOT NULL in ADD COLUMNS
        "expect_rows": None,
        "iceberg_note": (
            "Iceberg spec allows required fields, but Athena engine v3 does not "
            "implement NOT NULL in ALTER TABLE ADD COLUMNS for Iceberg tables."
        ),
        "consumer_notes": {
            "athena": "Rejects with InvalidRequestException — NOT NULL not supported in ADD COLUMNS.",
            "redshift_spectrum": "Spectrum may also reject NOT NULL in external table column additions.",
            "glue_etl": "Glue DynamicFrame does not enforce NOT NULL at read time.",
        },
    },

    # ── CHAOS ─────────────────────────────────────────────────────────────────

    {
        "name": "narrow_type_bigint_to_int",
        "category": "chaos",
        "title": "Narrow type: BIGINT → INT (type demotion — silently accepted!)",
        "description": (
            "Type demotion is NOT in the Iceberg allowed-promotions list, "
            "yet Athena engine v3 ACCEPTS this ALTER TABLE without error. "
            "Values within INT range survive intact; any value > 2,147,483,647 "
            "would be silently truncated on read. "
            "This is a live demonstration of silent data corruption risk: "
            "the DDL succeeds, queries return rows, but the type contract is violated."
        ),
        "ddl": "ALTER TABLE {db}.{table} CHANGE COLUMN record_count record_count INT",
        "validation_sql": "SELECT permit_id, record_count FROM {db}.{table} LIMIT 5",
        "expect_ddl_ok": True,   # Athena silently accepts this demotion — the dangerous finding
        "expect_rows": 5,
        "iceberg_note": (
            "Iceberg spec §3.11 lists allowed promotions (INT→LONG, FLOAT→DOUBLE, etc.); "
            "demotion is NOT listed. Athena accepting this is non-spec behaviour."
        ),
        "consumer_notes": {
            "athena": (
                "ACCEPTS the ALTER silently. Values within INT range are intact. "
                "Values exceeding INT_MAX would be truncated on read — silent data loss."
            ),
            "redshift_spectrum": "Would likely reject incompatible type at CREATE EXTERNAL TABLE.",
            "glue_etl": "Glue Schema Registry flags non-backward-compatible changes; ETL job may fail.",
        },
    },
    {
        "name": "incompatible_type_change",
        "category": "chaos",
        "title": "Incompatible type change: STRING → DATE (will fail)",
        "description": (
            "Changing a STRING column to DATE is never allowed in Iceberg — "
            "there is no lossless conversion path. "
            "The ALTER TABLE should be rejected by the engine. "
            "Attempting this in production could corrupt the table metadata "
            "if a buggy engine accepts it."
        ),
        "ddl": "ALTER TABLE {db}.{table} CHANGE COLUMN permit_type permit_type DATE",
        "validation_sql": "SELECT permit_id, permit_type FROM {db}.{table} LIMIT 1",
        "expect_ddl_ok": False,
        "expect_rows": None,
        "iceberg_note": "STRING→DATE is not in Iceberg allowed type promotions.",
        "consumer_notes": {
            "athena": "Rejects ALTER with type-incompatibility error. Table intact.",
            "redshift_spectrum": "Would fail to bind the column in the external table.",
            "glue_etl": "Schema Registry rejects as non-backward-compatible.",
        },
    },
    {
        "name": "rename_to_existing_name",
        "category": "chaos",
        "title": "Rename column to an already-existing name (collision)",
        "description": (
            "Renaming column A to the same name as column B creates a name collision. "
            "Iceberg tracks by field ID so both fields coexist internally, "
            "but SQL consumers that address by name will behave unpredictably."
        ),
        "ddl": "ALTER TABLE {db}.{table} RENAME COLUMN address TO permit_id",
        "validation_sql": "SELECT permit_id FROM {db}.{table} LIMIT 1",
        "expect_ddl_ok": False,  # most engines reject this
        "expect_rows": None,
        "iceberg_note": (
            "Iceberg spec does not explicitly prohibit duplicate names but "
            "most implementations reject it."
        ),
        "consumer_notes": {
            "athena": "Should reject with 'column already exists' error.",
            "redshift_spectrum": "Would create ambiguity; likely rejects at bind time.",
            "glue_etl": "DynamicFrame field resolution by name becomes ambiguous.",
        },
    },
    {
        "name": "drop_partition_column",
        "category": "chaos",
        "title": "Drop a column used in the partition spec",
        "description": (
            "If a column is part of the Iceberg partition spec, dropping it "
            "breaks the partition metadata. Athena should reject this. "
            "Forcing through on some engines corrupts partition pruning — "
            "full table scans silently replace predicate pushdown."
        ),
        "ddl": "ALTER TABLE {db}.{table} DROP COLUMN permit_type",
        "validation_sql": (
            "SELECT permit_id FROM {db}.{table} "
            "WHERE permit_type = 'BUILDING' LIMIT 1"
        ),
        "expect_ddl_ok": False,
        "expect_rows": None,
        "iceberg_note": (
            "Iceberg disallows dropping a field referenced in the active partition spec."
        ),
        "consumer_notes": {
            "athena": "Rejects ALTER — column is part of partition spec.",
            "redshift_spectrum": "Would silently degrade to full scan if partition column gone.",
            "glue_etl": "Partition pruning broken; Glue job cost spikes.",
        },
    },
]

# Ordered names for easy CLI selection
ALL_NAMES = [m["name"] for m in MUTATIONS]
SAFE_NAMES = [m["name"] for m in MUTATIONS if m["category"] == "safe"]
UNSAFE_NAMES = [m["name"] for m in MUTATIONS if m["category"] == "unsafe"]
CHAOS_NAMES = [m["name"] for m in MUTATIONS if m["category"] == "chaos"]
