"""Data-quality check on the curated table.

Real implementations would call Amazon Deequ (see
05_bigdata/glue-deequ-data-quality/) or Great Expectations / Soda. This
stub just demonstrates the shape: read the curated table, assert two
contracts, fail loud (non-zero exit) if violated so Step Functions retries
or alarms.
"""
from __future__ import annotations

import sys


def main() -> None:
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext

    args = getResolvedOptions(sys.argv, ["curated_db"])
    glue = GlueContext(SparkContext.getOrCreate())
    spark = glue.spark_session

    df = spark.table(f"glue_catalog.{args['curated_db']}.region_revenue")

    # Contract 1: at least one row.
    if df.count() == 0:
        raise SystemExit("DQ failure: region_revenue is empty")

    # Contract 2: revenue is non-negative.
    bad = df.filter("revenue < 0").count()
    if bad > 0:
        raise SystemExit(f"DQ failure: {bad} rows with negative revenue")


if __name__ == "__main__":
    main()
