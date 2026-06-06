# progressive-medallion-starter-kit

A single `cdk deploy` that provisions the complete 7-stage progressive medallion data lake architecture from *Data Engineering with AWS and Generative AI* (Chapter 3). Every stage, every Glue database, every Lake Formation tag, and a working CDC ingest pipeline — all deployed in one shot.

## Architecture

```
CoinGecko / Source API
        │
        ▼ (EventBridge — every 15 min)
Lambda: cdc_ingest.py
        │  writes newline-delimited JSON
        │  INSERT / UPDATE / DELETE records with _cdc_op, _cdc_ts metadata
        ▼
S3: datalake-landing-zone-[account]-[region]
  cdc/chicago_buildings/year=YYYY/month=MM/day=DD/hour=HH/batch_*.json
        │
        │  (also copied to Audit for WORM compliance)
        ▼
S3: datalake-audit-[account]-[region]          ← S3 Object Lock (WORM)
        │
        ▼ Glue Crawler (on demand / scheduled)
S3: datalake-raw-[account]-[region]            ← flat-file text, cataloged
  Glue DB: chicago-buildings-raw               ← Athena ad-hoc (text-based)
        │
        ▼
S3: datalake-optimized-[account]-[region]      ← Parquet, light transforms
  Glue DB: chicago-buildings-optimized         ← Athena ad-hoc (fast)
        │
        ▼
S3: datalake-conform-[account]-[region]        ← validated, quality-checked
  Glue DB: chicago-buildings-conform
        │
        ▼
S3: datalake-unified-analytics-[account]-[region]  ← integrated, denormalized
  Glue DB: chicago-buildings-unified-analytics
        │
        ▼
S3: datalake-published-[account]-[region]      ← highest quality, reporting
  Glue DB: chicago-buildings-published

All cataloged databases tagged via Lake Formation LF-Tags:
  data_domain       = buildings
  data_sensitivity  = internal | public
  medallion_stage   = raw | optimized | conform | unified-analytics | published
```

## The 7 Stages

| Stage | Bucket suffix | Glue DB | Purpose |
|---|---|---|---|
| **Landing Zone** | `landing-zone` | — | Raw CDC ingest. PII-risk zone. ELT entry point. |
| **Audit** | `audit` | — | WORM copy via S3 Object Lock. Immutable compliance record. |
| **Raw** | `raw` | `chicago-buildings-raw` | Flat-file text, cataloged. Troubleshooting & lineage. |
| **Optimized** | `optimized` | `chicago-buildings-optimized` | Parquet conversion + light transforms. Athena ad-hoc. |
| **Conform** | `conform` | `chicago-buildings-conform` | Data validation rules (Deequ-ready). Quality gate. |
| **Unified Analytics** | `unified-analytics` | `chicago-buildings-unified-analytics` | Integration, denormalization, aggregations. |
| **Published** | `published` | `chicago-buildings-published` | Highest quality. Business reporting. |

Bronze = Landing Zone + Raw. Silver = Optimized + Conform. Gold = Unified Analytics + Published.

## Bucket Naming Conventions

The stack supports four bucket naming conventions, selected via the `bucket_naming` CDK context key. Account ID and region are always resolved dynamically at synth time — no hardcoding.

### Option 1: `stage-only` (default)

```
datalake-[stage]-[account]-[region]
```

```
datalake-landing-zone-123456789012-us-west-2
datalake-audit-123456789012-us-west-2
datalake-raw-123456789012-us-west-2
datalake-optimized-123456789012-us-west-2
datalake-conform-123456789012-us-west-2
datalake-unified-analytics-123456789012-us-west-2
datalake-published-123456789012-us-west-2
```

Best for: single-org single-environment deployments. Clean, minimal, sorts well in the S3 console.

### Option 2: `org-env`

```
[org]-[env]-[stage]-[account]-[region]
```

```
acme-prod-landing-zone-123456789012-us-west-2
acme-prod-audit-123456789012-us-west-2
acme-prod-raw-123456789012-us-west-2
acme-dev-raw-123456789012-us-west-2   ← dev environment
```

Best for: multi-environment setups (dev/staging/prod) within the same or multiple accounts. Eliminates ambiguity when cost explorer, CloudTrail, or billing alerts show bucket names across environments.

Deploy:
```bash
cdk deploy --context bucket_naming=org-env --context org=acme --context env=prod
```

### Option 3: `provider-product`

```
[provider]-[product]-[stage]-[account]-[region]
```

```
chicago-buildings-landing-zone-123456789012-us-west-2
chicago-buildings-raw-123456789012-us-west-2
chicago-buildings-conform-123456789012-us-west-2
```

Best for: data lakes that house multiple data products from different providers. Mirrors the book's `[provider]-[product]-[stage]` Glue database convention at the S3 level — every bucket and database share the same prefix, making cross-layer tracing trivial.

Deploy:
```bash
cdk deploy --context bucket_naming=provider-product
```

### Option 4: `cdk-generated`

```
progressivemedallionstack-stagebucketraw37924d20-1abc2def3ghi
```

CDK assigns a random hash suffix. Globally unique without any configuration.

Best for: throwaway or ephemeral deployments where human-readable names don't matter. **Not recommended for production** — names are unreadable in cost reports, CloudTrail, and monitoring dashboards.

Deploy:
```bash
cdk deploy --context bucket_naming=cdk-generated
```

### Comparison

| Convention | Readable | Multi-env safe | Multi-product safe | Config required |
|---|---|---|---|---|
| `stage-only` | ✅ | ⚠️ same-env only | ⚠️ same-product only | none |
| `org-env` | ✅ | ✅ | ⚠️ same-product only | `--context org=` `env=` |
| `provider-product` | ✅ | ⚠️ same-env only | ✅ | none |
| `cdk-generated` | ❌ | ✅ | ✅ | none |

## Glue Database Naming Convention

Follows the book's `[provider]-[product]-[stage]` pattern:

```
chicago-buildings-raw
chicago-buildings-optimized
chicago-buildings-conform
chicago-buildings-unified-analytics
chicago-buildings-published
```

## Lake Formation Tag-Based Access Control

Three LF-Tag dimensions are provisioned and assigned to every cataloged database:

| Tag key | Values | Purpose |
|---|---|---|
| `data_domain` | `buildings`, `permits`, `infrastructure`, `finance` | Business domain classification |
| `data_sensitivity` | `public`, `internal`, `confidential`, `restricted` | Governs column/row-level access grants |
| `medallion_stage` | `raw`, `optimized`, `conform`, `unified-analytics`, `published` | Pipeline stage identification |

Published and Unified Analytics databases are tagged `data_sensitivity=public`. All earlier stages are `internal`.

Grant access to a principal by LF-Tags without touching IAM:
```bash
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::ACCOUNT:role/AnalystRole \
  --resource '{"LFTagPolicy":{"ResourceType":"DATABASE","Expression":[{"TagKey":"medallion_stage","TagValues":["published"]}]}}' \
  --permissions DESCRIBE
```

## CDC Ingest Pipeline

`lambda/cdc_ingest.py` simulates Change Data Capture from a source system, writing batches of newline-delimited JSON to the Landing Zone bucket. Each record includes:

```json
{
  "_cdc_op": "INSERT",
  "_cdc_ts": "2026-05-13T01:57:34Z",
  "_cdc_source": "chicago_buildings",
  "building_id": "CHI-005",
  "address": "225 W Randolph St",
  "year_built": 1963,
  "floors": 19,
  "use_type": "Office"
}
```

Operation distribution: ~60% INSERT, ~30% UPDATE, ~10% DELETE.

EventBridge triggers the Lambda every 15 minutes automatically. Invoke manually:
```bash
aws lambda invoke \
  --function-name <CdcFunctionName> \
  --payload '{"batch_size": 10}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```

## Project Layout

```
progressive-medallion-starter-kit/
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── medallion_stack.py    # all infrastructure
└── lambda/
    ├── cdc_ingest.py             # CDC batch ingest to Landing Zone
    └── requirements.txt
```

## Prerequisites

- AWS CLI configured (`aws configure`)
- CDK bootstrapped in your account/region (`cdk bootstrap`)
- Lake Formation data lake admin set for your IAM role/user (see Setup below)
- Python 3.12+, Node.js

## Setup

### 1. Grant Lake Formation admin

Before deploying, the CDK CloudFormation execution role and your own role need Lake Formation data lake admin status:

```bash
# Replace with your actual role ARNs
aws lakeformation put-data-lake-settings --data-lake-settings '{
  "DataLakeAdmins": [
    {"DataLakePrincipalIdentifier": "arn:aws:iam::ACCOUNT:role/cdk-hnb659fds-cfn-exec-role-ACCOUNT-REGION"},
    {"DataLakePrincipalIdentifier": "arn:aws:iam::ACCOUNT:role/YourAdminRole"}
  ],
  "CreateTableDefaultPermissions": [],
  "CreateDatabaseDefaultPermissions": []
}'
```

Also grant the Glue crawler role Lake Formation permissions (needed for crawlers to write to Glue catalog in LF mode):
```bash
GLUE_ROLE=arn:aws:iam::ACCOUNT:role/ProgressiveMedallionStack-GlueCrawlerRole-XXXXX

for DB in chicago-buildings-raw chicago-buildings-optimized chicago-buildings-conform \
          chicago-buildings-unified-analytics chicago-buildings-published; do
  aws lakeformation grant-permissions \
    --principal "DataLakePrincipalIdentifier=${GLUE_ROLE}" \
    --resource "Database={Name=${DB}}" \
    --permissions CREATE_TABLE DESCRIBE ALTER DROP
  aws lakeformation grant-permissions \
    --principal "DataLakePrincipalIdentifier=${GLUE_ROLE}" \
    --resource "Table={DatabaseName=${DB},TableWildcard={}}" \
    --permissions ALL
done
```

### 2. Deploy

```bash
cd cdk
python3 -m venv .venv && source .venv/bin/activate
pip install aws-cdk-lib constructs
cdk bootstrap   # first time only

# Default (stage-only naming)
cdk deploy --parameters AlertEmail=you@example.com

# With explicit naming convention
cdk deploy \
  --context bucket_naming=org-env \
  --context org=acme \
  --context env=prod \
  --parameters AlertEmail=you@example.com
```

`AlertEmail` is optional. `bucket_naming` defaults to `stage-only` if omitted. See [Bucket Naming Conventions](#bucket-naming-conventions) for all options.

## Parameters

### CloudFormation parameters (`--parameters`)

| Parameter | Default | Description |
|---|---|---|
| `AlertEmail` | *(empty)* | Email for SNS pipeline alerts |
| `SourceTable` | `chicago_buildings` | CDC source table name prefix in S3 paths |

### CDK context keys (`--context`)

| Key | Default | Description |
|---|---|---|
| `bucket_naming` | `stage-only` | Bucket naming convention: `stage-only`, `org-env`, `provider-product`, `cdk-generated` |
| `org` | `myorg` | Organisation prefix — used only when `bucket_naming=org-env` |
| `env` | `prod` | Environment label — used only when `bucket_naming=org-env` |

## Testing

### Trigger CDC ingest manually

```bash
# Get function name from stack outputs
FUNC=$(aws cloudformation describe-stacks \
  --stack-name ProgressiveMedallionStack \
  --query 'Stacks[0].Outputs[?OutputKey==`CdcFunctionName`].OutputValue' \
  --output text)

aws lambda invoke \
  --function-name "$FUNC" \
  --payload '{"batch_size": 5}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json && cat /tmp/response.json
```

### Verify data in Landing Zone

```bash
aws s3 ls s3://datalake-landing-zone-$(aws sts get-caller-identity --query Account --output text)-$(aws configure get region)/cdc/ --recursive
```

### Run the Raw stage crawler

```bash
aws glue start-crawler --name medallion-raw-crawler

# Poll until READY
watch -n 5 'aws glue get-crawler --name medallion-raw-crawler --query "Crawler.State" --output text'
```

### Query with Athena

```sql
-- After running the raw crawler, query the cataloged table
SELECT _cdc_op, count(*) as count
FROM "chicago-buildings-raw"."chicago_buildings"
GROUP BY _cdc_op;

-- Time-range partition pruning
SELECT *
FROM "chicago-buildings-raw"."chicago_buildings"
WHERE year = 2026
  AND month = 5
  AND hour >= 2
LIMIT 20;
```

Use the `medallion` Athena workgroup:
```bash
aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM \"chicago-buildings-raw\".\"chicago_buildings\"" \
  --work-group medallion \
  --query-execution-context Database=chicago-buildings-raw
```

## IAM Permissions Summary

| Principal | Permissions |
|---|---|
| CDC Lambda role | `AWSLambdaBasicExecutionRole` + `s3:PutObject` on Landing Zone + Audit buckets |
| Glue crawler role | `AWSGlueServiceRole` + `s3:GetObject` on all cataloged stage buckets |
| Lake Formation service | Registered S3 locations for all cataloged stages |

## Tear Down

```bash
cdk destroy
```

Buckets have `RemovalPolicy.RETAIN` — they are preserved on stack deletion. Delete them manually if no longer needed:
```bash
for STAGE in landing-zone audit raw optimized conform unified-analytics published athena-results; do
  BUCKET="datalake-${STAGE}-$(aws sts get-caller-identity --query Account --output text)-$(aws configure get region)"
  aws s3 rb "s3://${BUCKET}" --force
done
```
