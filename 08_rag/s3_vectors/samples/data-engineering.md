# Lakehouse Storage

Modern lakehouse architectures store raw and curated data in S3 using open
table formats such as Apache Iceberg. Iceberg tables track snapshots,
partitions, and column statistics in a metadata layer separate from the data
files, enabling time travel queries and incremental ingest.

## Compaction

As small files accumulate from streaming ingest, periodic compaction merges
them into larger Parquet files. This improves scan performance and reduces
the metadata footprint that query engines must load.

# Stream Ingestion

Amazon Kinesis Data Streams provides a durable, ordered, partitioned log for
real-time ingest. Consumers can be Lambda, Firehose, or KCL workers; each
checkpoints its position so failures don't drop or duplicate records.

## Schema evolution

Streaming pipelines often pair Kinesis with a schema registry so producers
can evolve event shapes without breaking downstream consumers. The registry
also lets analytics teams reason about historical data with confidence.

# Orchestration

Amazon Managed Workflows for Apache Airflow (MWAA) hosts production Airflow
DAGs without operating the scheduler or workers yourself. DAGs commonly
trigger Glue, EMR Serverless, or Athena to materialize curated datasets on a
schedule or in response to upstream events.
