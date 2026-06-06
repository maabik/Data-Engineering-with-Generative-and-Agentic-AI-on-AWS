"""Unit test for the curate_orders transformation.

Runs against a local SparkSession in <2 seconds. No AWS, no Glue.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pyspark.sql import SparkSession

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "glue_jobs"))

from curate_orders import transform  # noqa: E402


@pytest.fixture(scope="module")
def spark() -> SparkSession:
    return (
        SparkSession.builder
        .master("local[1]")
        .appName("test_curate_orders")
        .getOrCreate()
    )


def test_groups_by_region_and_excludes_cancelled(spark: SparkSession) -> None:
    orders = spark.createDataFrame(
        [
            (1, "c1", 10.0, "paid"),
            (2, "c2", 25.5, "paid"),
            (3, "c1", 5.0, "cancelled"),
            (4, "c1", 15.0, "paid"),
        ],
        ["order_id", "customer_id", "total", "status"],
    )
    customers = spark.createDataFrame(
        [("c1", "EU"), ("c2", "NA")],
        ["customer_id", "region"],
    )

    revenue = {row["region"]: row["revenue"] for row in transform(orders, customers).collect()}

    assert revenue == {"EU": 25.0, "NA": 25.5}


def test_empty_orders_returns_empty(spark: SparkSession) -> None:
    orders = spark.createDataFrame(
        [], "order_id INT, customer_id STRING, total DOUBLE, status STRING"
    )
    customers = spark.createDataFrame([("c1", "EU")], ["customer_id", "region"])

    assert transform(orders, customers).count() == 0
