# data-catalog

Bitcoin prices data catalog demonstrating automated ingestion, Glue cataloging, generative AI schema annotation, schema drift detection, and governance metadata tagging on AWS.

## Architecture

```
CoinDesk API
     │
     ▼ (hourly EventBridge)
Lambda: ingest.py
     │  writes time-partitioned JSON
     ▼
S3 bucket
  bitcoin_prices/
    year=YYYY/month=MM/day=DD/hour=HH/data.json
     │
     ▼ (hourly EventBridge — independent schedule)
Glue Crawler: bitcoin-prices-crawler
  - detects Hive partitions automatically
  - merges new columns, inherits partition schema
     │
     ▼ (EventBridge: crawler SUCCEEDED)
Lambda: annotate.py
  ├── 1. Load schema from Glue catalog
  ├── 2. Diff against S3 snapshot → SNS alert if drifted
  ├── 3. Save new snapshot to S3
  ├── 4. Call Bedrock (Claude) → generate table + column descriptions
  ├── 5. Write descriptions back to Glue catalog
  └── 6. Tag table: owner, data_classification, source_system, sla_tier
```

## Features

### Time-partitioned S3 layout
Data lands under `year=/month=/day=/hour=` prefixes. The Glue crawler auto-detects these as Hive partition keys, enabling Athena partition pruning and dramatically reducing scanned data on time-range queries.

### Generative AI schema annotation (Bedrock)
After each successful crawl, Claude reads the live Glue table schema and writes human-readable descriptions for the table and every column back into the Glue Data Catalog. Zero manual documentation effort.

### Schema drift detection
Each run diffs the current schema against a JSON snapshot stored in S3. Added columns, removed columns, and type changes trigger an SNS notification to alert downstream consumers before they break.

### Automated governance metadata tagging
Every crawl stamps the Glue table with `owner`, `data_classification`, `source_system`, and `sla_tier` parameters — making tables discoverable and governable via AWS Glue Data Catalog search and Lake Formation.

## Project layout

```
data-catalog/
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── data_catalog_stack.py   # all infrastructure
└── lambda/
    ├── ingest.py                   # fetch API → S3 (time-partitioned)
    ├── annotate.py                 # drift detection + Bedrock annotation + tagging
    └── requirements.txt
```

## Deploy

```bash
cd cdk
python -m venv .venv && source .venv/bin/activate
pip install aws-cdk-lib constructs
cdk bootstrap   # first time only
cdk deploy \
  --parameters AlertEmail=you@example.com \
  --parameters MetadataOwner=data-engineering \
  --parameters MetadataSLATier=bronze
```

`AlertEmail` is optional — omit it to skip email drift notifications.

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `ApiUrl` | CoinGecko simple price endpoint | Source API for Bitcoin price data |
| `AlertEmail` | *(empty)* | Email for schema drift SNS alerts |
| `BedrockModelId` | `us.anthropic.claude-sonnet-4-6` | Inference profile used for schema annotation |
| `MetadataOwner` | `data-engineering` | Glue table `owner` tag |
| `MetadataSLATier` | `bronze` | Glue table `sla_tier` tag |

## Query with Athena

```sql
-- partition pruning: only scans the last 24 hours
SELECT *
FROM bitcoin_prices.bitcoin_prices
WHERE year = 2025
  AND month = 5
  AND day = 12
  AND hour >= 10;
```

## IAM permissions summary

| Principal | Permissions |
|---|---|
| Glue crawler role | `AWSGlueServiceRole` + `s3:GetObject` on data bucket |
| Ingest Lambda role | `AWSLambdaBasicExecutionRole` + `s3:PutObject` on data bucket |
| Annotate Lambda role | `AWSLambdaBasicExecutionRole` + S3 read/write (snapshots) + `glue:GetTable/UpdateTable` + `bedrock:InvokeModel` + `sns:Publish` |
