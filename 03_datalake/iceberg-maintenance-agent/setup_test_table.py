#!/usr/bin/env python3
"""
Create a live Iceberg test table in AWS with:
- Many small files (triggers compaction)
- Many snapshots (triggers expiration)

Table: iceberg_debugger.maintenance_test
Bucket: icebergdebuggerstack-databuckete3889a50-qamhbqkafffi
"""
import sys
sys.path.insert(0, 'src')

import pyarrow as pa
from pyiceberg.catalog.glue import GlueCatalog
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType, LongType, DoubleType, TimestampType
from pyiceberg.partitioning import UNPARTITIONED_PARTITION_SPEC
from pyiceberg.table.sorting import UNSORTED_SORT_ORDER
from datetime import datetime, timezone
import uuid

BUCKET = "icebergdebuggerstack-databuckete3889a50-qamhbqkafffi"
NAMESPACE = "iceberg_debugger"
TABLE_NAME = "maintenance_test"
REGION = "us-east-1"
NUM_BATCHES = 30  # 30 separate appends -> 30 snapshots, each with a small file

def main():
    catalog = GlueCatalog("maintenance_catalog", **{"region_name": REGION})

    schema = Schema(
        NestedField(1, "event_id", StringType(), required=False),
        NestedField(2, "user_id", LongType(), required=False),
        NestedField(3, "event_type", StringType(), required=False),
        NestedField(4, "value", DoubleType(), required=False),
        NestedField(5, "event_ts", TimestampType(), required=False),
    )

    table_id = f"{NAMESPACE}.{TABLE_NAME}"
    location = f"s3://{BUCKET}/iceberg/{TABLE_NAME}"

    # Drop if exists
    try:
        catalog.drop_table(table_id)
        print(f"Dropped existing table {table_id}")
    except Exception:
        pass

    print(f"Creating table {table_id} at {location} ...")
    table = catalog.create_table(
        identifier=table_id,
        schema=schema,
        location=location,
        partition_spec=UNPARTITIONED_PARTITION_SPEC,
        sort_order=UNSORTED_SORT_ORDER,
        properties={
            "write.parquet.compression-codec": "snappy",
            # Artificially small target to ensure many files
            "write.target-file-size-bytes": str(64 * 1024),  # 64KB
        },
    )
    print(f"Table created. Writing {NUM_BATCHES} small batches...")

    now = datetime.now(timezone.utc)
    for i in range(NUM_BATCHES):
        batch_data = {
            "event_id":   [str(uuid.uuid4()) for _ in range(50)],
            "user_id":    [i * 50 + j for j in range(50)],
            "event_type": [["click", "view", "purchase"][j % 3] for j in range(50)],
            "value":      [float(i * 100 + j) for j in range(50)],
            "event_ts":   [now] * 50,
        }
        arrow_table = pa.table(batch_data, schema=pa.schema([
            pa.field("event_id",   pa.string()),
            pa.field("user_id",    pa.int64()),
            pa.field("event_type", pa.string()),
            pa.field("value",      pa.float64()),
            pa.field("event_ts",   pa.timestamp("us", tz="UTC")),
        ]))
        table.append(arrow_table)
        if (i + 1) % 5 == 0:
            print(f"  Written batch {i+1}/{NUM_BATCHES}")

    # Verify
    snap_count = len(table.snapshots())
    files = list(table.scan().plan_files())
    print(f"\nTable ready:")
    print(f"  Snapshots : {snap_count}")
    print(f"  Data files: {len(files)}")
    print(f"  Location  : {location}")
    print(f"\nTable identifier for agent config: s3://{BUCKET}/iceberg/{TABLE_NAME}")
    return location

if __name__ == "__main__":
    main()
