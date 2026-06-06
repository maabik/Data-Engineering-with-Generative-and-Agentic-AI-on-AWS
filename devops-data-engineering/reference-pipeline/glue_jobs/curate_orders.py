"""Curate raw orders into a partitioned Iceberg table.

The transformation is a pure function so it's unit-testable without AWS.
The Glue wrapper at the bottom is what ties it to GlueContext + S3.
See ../docs/testing-data-pipelines.md for why the structure matters.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import DataFrame


def transform(orders_df: "DataFrame", customers_df: "DataFrame") -> "DataFrame":
    """Pure function: orders + customers -> per-region revenue.

    No GlueContext, no S3, no boto3. Tested in
    tests/unit/test_curate_orders.py against a local SparkSession.
    """
    from pyspark.sql import functions as F

    return (
        orders_df.alias("o")
        .join(customers_df.alias("c"), F.col("o.customer_id") == F.col("c.customer_id"))
        .filter(F.col("o.status") != "cancelled")
        .groupBy(F.col("c.region").alias("region"))
        .agg(F.sum("o.total").alias("revenue"))
    )


def main() -> None:
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext

    args = getResolvedOptions(
        sys.argv,
        ["raw_bucket", "curated_bucket", "curated_db"],
    )

    glue = GlueContext(SparkContext.getOrCreate())
    spark = glue.spark_session

    orders = spark.read.parquet(f"s3://{args['raw_bucket']}/orders/")
    customers = spark.read.parquet(f"s3://{args['raw_bucket']}/customers/")

    revenue = transform(orders, customers)

    target = f"glue_catalog.{args['curated_db']}.region_revenue"
    (
        revenue.writeTo(target)
        .using("iceberg")
        .tableProperty("location", f"s3://{args['curated_bucket']}/region_revenue/")
        .createOrReplace()
    )


if __name__ == "__main__":
    main()
