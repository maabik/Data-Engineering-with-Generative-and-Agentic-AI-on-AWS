"""Persistent Glue PySpark job: raw CSV -> curated Parquet.

Reads --SOURCE_URI, writes Parquet partitioned by --PARTITION_COLS to
--TARGET_URI, and updates the Glue Catalog at --DATABASE.--TABLE so
partitions appear automatically. Uses the Glue DynamicFrame sink with
enableUpdateCatalog=True instead of Spark SQL `MSCK REPAIR TABLE`,
which avoids the default-database Lake Formation gate.

Arguments:
    --SOURCE_URI       s3://bucket/landing/file.csv
    --TARGET_URI       s3://bucket/curated/<table>/
    --DATABASE         glue catalog database
    --TABLE            glue catalog table (curated)
    --PARTITION_COLS   comma-separated partition column names ('' for none)
    --SOURCE_FORMAT    csv | json | parquet (default csv)
"""
import sys

from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext


REQUIRED = [
    "JOB_NAME",
    "SOURCE_URI",
    "TARGET_URI",
    "DATABASE",
    "TABLE",
    "PARTITION_COLS",
    "SOURCE_FORMAT",
]


def _safe(name: str) -> str:
    out = []
    for ch in name.strip().lower():
        if ch.isalnum() or ch == "_":
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "col"


def main():
    args = getResolvedOptions(sys.argv, REQUIRED)

    sc = SparkContext.getOrCreate()
    glue = GlueContext(sc)
    spark = glue.spark_session

    src = args["SOURCE_URI"]
    tgt = args["TARGET_URI"].rstrip("/") + "/"
    database = args["DATABASE"]
    table = args["TABLE"]
    partition_cols = [
        _safe(c) for c in (args.get("PARTITION_COLS") or "").split(",") if c.strip()
    ]
    fmt = (args.get("SOURCE_FORMAT") or "csv").lower()

    if fmt == "csv":
        df = (
            spark.read.option("header", "true")
            .option("inferSchema", "true")
            .csv(src)
        )
    elif fmt == "json":
        df = spark.read.json(src)
    elif fmt == "parquet":
        df = spark.read.parquet(src)
    else:
        raise ValueError(f"unsupported SOURCE_FORMAT {fmt!r}")

    for c in df.columns:
        safe = _safe(c)
        if safe != c:
            df = df.withColumnRenamed(c, safe)

    print(f"input rows: {df.count()}")
    df.printSchema()

    dyf = DynamicFrame.fromDF(df, glue, "curated")

    sink = glue.getSink(
        connection_type="s3",
        path=tgt,
        enableUpdateCatalog=True,
        updateBehavior="UPDATE_IN_DATABASE",
        partitionKeys=partition_cols,
    )
    sink.setFormat("glueparquet")
    sink.setCatalogInfo(catalogDatabase=database, catalogTableName=table)
    sink.writeFrame(dyf)
    print(f"wrote curated parquet to {tgt} and updated catalog {database}.{table}")


if __name__ == "__main__":
    main()
