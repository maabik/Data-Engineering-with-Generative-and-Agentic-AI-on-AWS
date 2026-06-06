"""Glue PySpark job: PyDeequ analysis + verification on a CSV dataset.

Job arguments (passed via --JOB_NAME, --S3_INPUT, --RESULTS_TABLE, --SPARK_VERSION):
  S3_INPUT       s3://bucket/path/to/file.csv
  RESULTS_TABLE  fully qualified Iceberg/Glue table for DQ results
                 (catalog.namespace.table). Optional - if omitted, results are
                 only logged.
  SPARK_VERSION  Spark major.minor matching the Glue version (default 3.3)
"""
import logging
import os
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.functions import current_timestamp, lit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUIRED_ARGS = ["JOB_NAME", "S3_INPUT"]
OPTIONAL_ARGS = ["RESULTS_TABLE", "SPARK_VERSION"]

provided = set(sys.argv)
optional_present = [a for a in OPTIONAL_ARGS if any(arg.startswith(f"--{a}") for arg in provided)]
args = getResolvedOptions(sys.argv, REQUIRED_ARGS + optional_present)

os.environ["SPARK_VERSION"] = args.get("SPARK_VERSION", "3.3")

from pydeequ.analyzers import (  # noqa: E402  (must follow SPARK_VERSION env var)
    AnalysisRunner,
    AnalyzerContext,
    Completeness,
    Distinctness,
    Size,
)
from pydeequ.checks import Check, CheckLevel  # noqa: E402
from pydeequ.verification import VerificationResult, VerificationSuite  # noqa: E402

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

s3_input = args["S3_INPUT"]
results_table = args.get("RESULTS_TABLE")

logger.info("Reading CSV from %s", s3_input)
df = (
    spark.read.format("csv")
    .option("header", "true")
    .option("escape", '"')
    .option("inferSchema", "true")
    .load(s3_input)
)

normalized = {c: c.strip().lower().replace(" ", "_") for c in df.columns}
for src, dst in normalized.items():
    if src != dst:
        df = df.withColumnRenamed(src, dst)

logger.info("Schema after normalization: %s", df.schema.simpleString())
df.show(5, truncate=False)

analysis_result = (
    AnalysisRunner(spark)
    .onData(df)
    .addAnalyzer(Size())
    .addAnalyzer(Completeness("tweet_id"))
    .addAnalyzer(Distinctness("tweet_id"))
    .addAnalyzer(Completeness("entity"))
    .addAnalyzer(Completeness("sentiment"))
    .run()
)
analysis_df = AnalyzerContext.successMetricsAsDataFrame(spark, analysis_result)
analysis_df.show(truncate=False)

check = (
    Check(spark, CheckLevel.Error, "Data Quality Check")
    .hasSize(lambda x: x >= 3)
    .isComplete("tweet_id")
    .isComplete("entity")
    .isComplete("sentiment")
    .isUnique("tweet_id")
)

verification = (
    VerificationSuite(spark)
    .onData(df)
    .addCheck(check)
    .run()
)
verification_df = VerificationResult.checkResultsAsDataFrame(spark, verification)
verification_df.show(truncate=False)

if results_table:
    logger.info("Persisting DQ results to %s", results_table)
    run_tag = (
        analysis_df.withColumn("run_at", current_timestamp())
        .withColumn("job_name", lit(args["JOB_NAME"]))
        .withColumn("source", lit(s3_input))
    )
    run_tag.writeTo(f"{results_table}_metrics").using("iceberg").append()

    verification_run = (
        verification_df.withColumn("run_at", current_timestamp())
        .withColumn("job_name", lit(args["JOB_NAME"]))
        .withColumn("source", lit(s3_input))
    )
    verification_run.writeTo(f"{results_table}_checks").using("iceberg").append()

failed = verification.status != "Success"
job.commit()
spark.sparkContext._gateway.shutdown_callback_server()

if failed:
    raise SystemExit(f"Deequ verification failed for {s3_input}")

logger.info("Deequ verification passed")
