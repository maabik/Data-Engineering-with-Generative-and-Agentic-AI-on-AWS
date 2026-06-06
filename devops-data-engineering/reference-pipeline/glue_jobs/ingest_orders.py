"""Ingestion job stub.

In a real pipeline this pulls from an upstream API or DB and lands raw
parquet under s3://<raw>/orders/ and /customers/. Kept minimal here — the
DevOps practices in the labs don't depend on the ingestion logic.
"""
from __future__ import annotations

import sys


def main() -> None:
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    from pyspark.sql import Row

    args = getResolvedOptions(sys.argv, ["raw_bucket"])
    glue = GlueContext(SparkContext.getOrCreate())
    spark = glue.spark_session

    orders = spark.createDataFrame([
        Row(order_id=1, customer_id="c1", total=10.0, status="paid"),
        Row(order_id=2, customer_id="c2", total=25.5, status="paid"),
        Row(order_id=3, customer_id="c1", total=5.0, status="cancelled"),
    ])
    customers = spark.createDataFrame([
        Row(customer_id="c1", region="EU"),
        Row(customer_id="c2", region="NA"),
    ])

    orders.write.mode("overwrite").parquet(f"s3://{args['raw_bucket']}/orders/")
    customers.write.mode("overwrite").parquet(f"s3://{args['raw_bucket']}/customers/")


if __name__ == "__main__":
    main()
