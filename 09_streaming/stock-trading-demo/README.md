# Stock Trading Stream Processing with S3 Tables Demo

A comprehensive real-time streaming data solution that processes simulated stock trades using AWS services, with S3 Tables and Apache Iceberg for ACID transactions and advanced analytics.

## 🏗️ Architecture Overview

```
┌─────────────────┐    ┌──────────────┐    ┌─────────────────┐
│  Trade Generator│───▶│ Kinesis      │───▶│ Kinesis Data    │
│  (Python)       │    │ Data Stream  │    │ Firehose        │
└─────────────────┘    └──────────────┘    │ KDS-s3tables-   │
                              │            │ trades          │
                              │            └─────────────────┘
                              │                       │
                              │                       ▼
                              │            ┌─────────────────┐
                              │            │ S3 Tables       │
                              │            │ (Apache Iceberg)│
                              │            │ trading/trades  │
                              │            └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐    ┌─────────────────┐
                    │ Lambda          │───▶│ Amazon Bedrock  │
                    │ (Trend Analysis)│    │ (Claude Haiku)  │
                    └─────────────────┘    └─────────────────┘
                              │                       │
                              ▼                       ▼
                    ┌─────────────────┐    ┌─────────────────┐
                    │ SNS             │    │ Trend Analysis  │
                    │ Notifications   │    │ Results         │
                    └─────────────────┘    └─────────────────┘
```

## 🎯 Features

- **Real-time Data Streaming**: Simulated stock trades via Kinesis Data Stream
- **ACID Transactions**: S3 Tables with Apache Iceberg for UPSERT operations
- **AI-Powered Analysis**: Amazon Bedrock Claude Haiku 4.5 for trend detection
- **Automated Notifications**: SNS alerts for detected trading trends
- **Scalable Architecture**: Serverless components that auto-scale
- **Time Travel Queries**: Historical data analysis capabilities
- **Schema Evolution**: Adapt to changing data requirements

## 📋 Prerequisites

### Required Tools
- **AWS CLI**: Version 2.x configured with appropriate credentials
- **AWS CDK**: Version 2.x installed globally (`npm install -g aws-cdk`)
- **Python**: Version 3.9 or higher
- **Node.js**: Version 18.x or higher (for CDK)
- **Git**: For cloning and version control

### AWS Permissions Required
Your AWS credentials must have permissions for:
- CloudFormation (full access)
- IAM (create/manage roles and policies)
- Kinesis (create/manage streams)
- Lambda (create/manage functions)
- S3 (create/manage buckets)
- SNS (create/manage topics)
- S3 Tables (create/manage tables and namespaces)
- Kinesis Data Firehose (create/manage delivery streams)
- Glue (create/manage databases and tables)
- Bedrock (invoke models)
- Lake Formation (manage permissions, register principals as DataLakePrincipal)
- Logs (CloudWatch access)
- Athena (create-data-catalog, start-query-execution)

### AWS Account Setup
```bash
# Verify AWS CLI configuration
aws sts get-caller-identity

# Check default region (should be us-east-1 or your preferred region)
aws configure get region
```

---

## ⚠️ Critical AWS Documentation Gaps

This section documents the non-obvious requirements that are **not** covered
in public AWS documentation but are required to make this pipeline work
end-to-end. Every item below is something we hit live during deployment and
had to reverse-engineer from CloudTrail, IAM Access Analyzer, or trial and
error. Read this section before debugging a failed deploy.

### 1. The S3 Tables federated Glue catalog hierarchy

When you create an S3 Tables bucket, AWS auto-creates a **federated** Glue
catalog under your account's root catalog. The hierarchy looks like:

```
<account-id>                       (root Glue catalog — your AWS account)
└── s3tablescatalog                (top-level federated catalog node)
    └── <s3-tables-bucket-name>    (one per S3 Tables bucket)
        └── <namespace>            (e.g. "trading")
            └── <table>            (e.g. "trades")
```

For this demo, the catalog id you must pass to Firehose, Lake Formation,
and Athena is:

```
<account-id>:s3tablescatalog/stock-trading-demo
```

Note the colon after the account id and the slash before the bucket name —
this **mixed delimiter** is the format Glue, LF, and Athena expect, and it
is what the Glue catalog ARN below resolves to when you reference the
federated catalog.

The Glue catalog ARN format for the federated S3 Tables catalog is:

```
arn:aws:glue:<region>:<account-id>:catalog/s3tablescatalog/<bucket-name>
```

Reference this ARN anywhere a Glue catalog ARN is needed for the federated
catalog (Firehose role permissions, LF data location resources, etc.).

### 2. Hidden IAM actions: `s3tables:GetTableBucket` and `s3tables:GetNamespace`

The public IAM permission examples for both Firehose and any Lambda querying
S3 Tables list `s3tables:GetTable`, `s3tables:UpdateTable`, etc. **They omit
two actions that the federation layer silently calls under the hood**:

- `s3tables:GetTableBucket`
- `s3tables:GetNamespace`

Without these, you'll see opaque errors like:
- Firehose: `AccessDeniedException: User ... is not authorized to perform: s3tables:GetTableBucket on resource: arn:aws:s3tables:...:bucket/stock-trading-demo`
- Athena (from Lambda): the underlying call is also `GetTableBucket`/`GetNamespace`, surfaced as a Lake Formation permission error.

Add **all four resource patterns** for these actions:

```json
{
  "Action": [
    "s3tables:GetTableBucket",
    "s3tables:GetNamespace",
    "s3tables:GetTable",
    "s3tables:GetTableData",
    "s3tables:UpdateTableData",
    "s3tables:PutTableData"
  ],
  "Resource": [
    "arn:aws:s3tables:REGION:ACCOUNT:bucket/stock-trading-demo",
    "arn:aws:s3tables:REGION:ACCOUNT:bucket/stock-trading-demo/*",
    "arn:aws:s3tables:REGION:ACCOUNT:bucket/stock-trading-demo/table/*",
    "arn:aws:s3tables:REGION:ACCOUNT:bucket/stock-trading-demo/namespace/*"
  ]
}
```

The `bucket/<name>` form (no trailing slash, no wildcard) is what
`GetTableBucket`/`GetNamespace` evaluate against — `bucket/<name>/*` alone
is not enough.

### 3. Lake Formation grants for federated catalogs must be issued out-of-band

Lake Formation governs read access to S3 Tables data. The Firehose
service-linked role for the Iceberg destination needs LF grants on the
catalog/database/table; **so does any Lambda or analyst querying through
Athena**.

The catch: CloudFormation's `AWS::LakeFormation::Permissions` resource type
**rejects** the `<account-id>:s3tablescatalog/<bucket-name>` catalog id
format because the resource schema validates `CatalogId` as a 12-character
account id. There is currently no CFN-supported way to issue an LF grant on
a federated catalog. The only working path is the CLI:

```bash
# DESCRIBE on the database (required for Athena to resolve the schema)
cat > /tmp/lf-db.json <<EOF
{
  "Principal": { "DataLakePrincipalIdentifier": "<role-arn>" },
  "Resource": {
    "Database": {
      "CatalogId": "<account-id>:s3tablescatalog/stock-trading-demo",
      "Name": "trading"
    }
  },
  "Permissions": ["DESCRIBE"]
}
EOF
aws lakeformation grant-permissions --cli-input-json file:///tmp/lf-db.json

# SELECT + DESCRIBE on the table itself
cat > /tmp/lf-tbl.json <<EOF
{
  "Principal": { "DataLakePrincipalIdentifier": "<role-arn>" },
  "Resource": {
    "Table": {
      "CatalogId": "<account-id>:s3tablescatalog/stock-trading-demo",
      "DatabaseName": "trading",
      "Name": "trades"
    }
  },
  "Permissions": ["SELECT", "DESCRIBE"]
}
EOF
aws lakeformation grant-permissions --cli-input-json file:///tmp/lf-tbl.json
```

The Firehose service-linked role gets its LF grants automatically when you
create the Iceberg destination in CDK (Firehose's CFN integration handles
that one specific case). Every other principal — Lambda execution roles,
analyst roles, data-engineering roles — needs the manual CLI grants above.

### 4. Athena DataCatalog binding for the federated S3 Tables catalog

Athena does not auto-discover federated Glue catalogs. To `SELECT FROM
trading.trades` from Athena (interactively or from Lambda), you must
register the federated catalog as an Athena `DataCatalog`:

```python
# CDK
athena.CfnDataCatalog(
    self, "S3TablesAthenaCatalog",
    name="s3tables_stock_trading",       # ← name you pass to Athena queries
    type="GLUE",
    description="S3 Tables federated catalog",
    parameters={
        "catalog-id": f"{account_id}:s3tablescatalog/stock-trading-demo",
    },
)
```

When running queries, you must then pass the registered name:

```python
athena.start_query_execution(
    QueryString="SELECT * FROM trading.trades LIMIT 10",
    QueryExecutionContext={
        "Catalog": "s3tables_stock_trading",   # NOT "AwsDataCatalog"
        "Database": "trading",
    },
    ...
)
```

Forgetting `Catalog` defaults to `AwsDataCatalog`, and Athena returns
`SCHEMA_NOT_FOUND: Schema 'trading' does not exist` even though Glue clearly
shows the database — because it's looking in the wrong catalog.

The Lambda role also needs `athena:GetDataCatalog` on the data-catalog ARN
(`arn:aws:athena:<region>:<account>:datacatalog/s3tables_stock_trading`).

### 5. Iceberg `timestamp` column format

The Iceberg `timestamp` type expects ISO 8601 with `T` separator and **no**
timezone suffix (it is `timestamp without timezone`). Common producer code
that emits `2026-05-27 03:22:23.673` (Python `str(datetime.now())`) is
rejected by Firehose with:

```
MalformedFieldWithinRecord
```

Use:

```python
datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
# → "2026-05-27T03:22:23.673"
```

### 6. Claude Haiku 4.5 requires a cross-region inference profile

The IAM example `arn:aws:bedrock:<region>::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`
is **not invokable on demand** — calling `InvokeModel`/`Converse` against
the foundation model returns:

```
ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0
with on-demand throughput isn't supported. Retry your request with the ID
or ARN of an inference profile that contains this model.
```

Switch the model id to the cross-region inference profile and grant access
to **both** the inference profile ARN and the underlying foundation models
across regions:

```python
BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"   # us. prefix = cross-region inference profile

resources = [
    f"arn:aws:bedrock:{region}:{account}:inference-profile/{BEDROCK_MODEL_ID}",
    f"arn:aws:bedrock:{region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",   # cross-region
]
```

Without the wildcard `*` foundation-model ARN, the inference-profile call
succeeds in your home region but fails intermittently when the routing
layer dispatches to a peer region.

### 7. Two-phase deploy via `--context skipFirehose=true` (IAM eventual consistency)

Firehose's Iceberg destination validates the role at create time by performing
a synchronous `s3tables:GetTableBucket` call. IAM is eventually consistent —
when CDK creates the Firehose role and the delivery stream in the same
deployment, the role often hasn't propagated by the time Firehose validates
it, producing:

```
The IAM role configured doesn't have access to write to S3 Tables.
```

The fix is a two-phase deploy. The first phase creates everything except the
Firehose stream; the second phase adds it once IAM has settled:

```bash
# Phase 1: stack with skipFirehose context flag
cdk deploy --context skipFirehose=true

# Wait ~30s for IAM to propagate, then:
cdk deploy
```

`deploy-complete-solution.sh` already does this. If you ever hit the role
validation error after a fresh deploy, you can also just re-run `cdk deploy`
30 seconds later — the second changeset will succeed.

### 8. Firehose buffer flush behavior

Firehose's Iceberg destination buffers up to **5 MB** OR **300 seconds**
before flushing. With a low-volume producer (~10 records/s of small JSON),
you may see no S3 Tables data for a full 5 minutes after starting the
producer. This is expected — it is **not** a permission or routing failure.

To force an early flush during smoke tests, push at least 500 small records
in a tight loop to trigger the size-based flush.

### 9. Required CloudWatch metrics for the Firehose Iceberg destination

The Iceberg-specific metrics are namespaced **with dots, not slashes**:

- `DeliveryToIceberg.SuccessfulRowCount`
- `DeliveryToIceberg.FailedRowCount`
- `DeliveryToIceberg.IncomingRowCount`

The legacy Firehose metrics (`DeliveryToS3.Success`) **do not fire** for the
Iceberg destination. Building an "is the pipeline working?" CloudWatch
dashboard against the legacy metrics will show 0 traffic even when data is
flowing fine.

---

## 🚀 Deployment Instructions

### Step 1: Clone and Setup Project

```bash
# Navigate to your development directory
cd /path/to/your/projects

# The project structure should look like this:
stock-trading-demo/
├── README.md
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── requirements.txt
├── lambda/
│   ├── trend_analyzer.py
│   └── requirements.txt
├── scripts/
│   ├── trade_generator.py
│   ├── monitor-kds-s3tables.py
│   └── requirements.txt
└── venv/
```

### Step 2: Deploy

Everything (Glue DB+Table, IAM, Firehose Iceberg destination, Lake Formation
grants, Lambda, CloudWatch dashboard, alarms, DLQ) is provisioned by CDK.

```bash
cd stock-trading-demo
./deploy-complete-solution.sh
```

The script:
1. Verifies the S3 Tables bucket / namespace / table exist (creates them if not).
2. Bootstraps CDK if needed.
3. Runs `cdk deploy`.

The S3 Tables bucket itself is created out-of-band because it isn't yet a
first-class CloudFormation resource.

#### S3 Tables Schema (provisioned in CDK)

| Column | Type |
|---|---|
| trade_id | string (unique key) |
| stock_symbol | string |
| trade_type | string |
| price | double |
| quantity | int |
| timestamp | timestamp |
| total_value | double |
| trader_id | string |
| exchange | string |
| sector | string |
| market_cap | string |
| volatility | double |
| volume | bigint |
| trend_signal | string |

### Step 3: Subscribe to SNS Notifications (Optional)

```bash
SNS_TOPIC=$(aws cloudformation describe-stacks --stack-name StockTradingStack \
    --query 'Stacks[0].Outputs[?OutputKey==`SNSTopicARN`].OutputValue' --output text)
aws sns subscribe --topic-arn "$SNS_TOPIC" \
    --protocol email --notification-endpoint "you@example.com"
```

### Step 4: Generate Trades

```bash
source venv/bin/activate
python3 scripts/trade_generator.py
```

## 📊 Verification Steps

### 1. Verify Kinesis Stream
```bash
# Check stream status
aws kinesis describe-stream --stream-name stock-trades-stream

# List streams
aws kinesis list-streams
```

### 2. Verify Firehose Delivery Stream
```bash
# Check delivery stream status
aws firehose describe-delivery-stream --delivery-stream-name KDS-s3tables-trades

# Monitor delivery logs
aws logs tail /aws/kinesisfirehose/KDS-s3tables-trades --follow
```

### 3. Verify Lambda Function
```bash
# List functions
aws lambda list-functions --query 'Functions[?contains(FunctionName, `TrendAnalyzer`)]'

# Check recent invocations
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/StockTradingStack"
```

### 4. Verify S3 Tables Data
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
aws s3tables list-tables \
    --table-bucket-arn "arn:aws:s3tables:${REGION}:${ACCOUNT_ID}:bucket/stock-trading-demo" \
    --namespace trading
```

### 5. Verify Iceberg Writes (three independent methods)

Because the Iceberg destination is silent on individual record failures and
the buffer takes up to 5 minutes to flush, verify writes through all three
of these channels — if any one shows progress, the pipeline is working:

```bash
# Method 1 — Firehose CloudWatch metrics (use the Iceberg-namespaced names)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Firehose \
  --metric-name DeliveryToIceberg.SuccessfulRowCount \
  --dimensions Name=DeliveryStreamName,Value=KDS-s3tables-trades \
  --start-time $(date -u -v-30M +%Y-%m-%dT%H:%M:%S) \
  --end-time   $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum

# Method 2 — S3 Tables metadata version (increments on every commit)
aws s3tables get-table \
  --table-bucket-arn arn:aws:s3tables:REGION:ACCOUNT:bucket/stock-trading-demo \
  --namespace trading --name trades \
  --query 'metadataLocation'

# Method 3 — Athena live read (definitive but slowest)
aws athena start-query-execution \
  --query-string "SELECT COUNT(DISTINCT trade_id) FROM trading.trades" \
  --query-execution-context Catalog=s3tables_stock_trading,Database=trading \
  --result-configuration OutputLocation=s3://YOUR-RESULTS-BUCKET/
```

### 6. Test UPSERT Functionality
```bash
# Generate test trades with duplicate IDs
cd scripts
python3 -c "
from trade_generator import TradeGenerator
import json
from datetime import datetime

generator = TradeGenerator('stock-trades-stream')

# Original trade
trade1 = {
    'trade_id': 'UPSERT-TEST-001',
    'stock_symbol': 'AAPL',
    'price': 150.00,
    'quantity': 100,
    'trade_type': 'BUY',
    'timestamp': datetime.now().isoformat(),
    'total_value': 15000.00
}

# Updated trade (same ID)
trade2 = {
    'trade_id': 'UPSERT-TEST-001',
    'stock_symbol': 'AAPL',
    'price': 155.50,
    'quantity': 100,
    'trade_type': 'BUY',
    'timestamp': datetime.now().isoformat(),
    'total_value': 15550.00
}

# Send both trades
generator.kinesis_client.put_record(
    StreamName='stock-trades-stream',
    Data=json.dumps(trade1),
    PartitionKey='AAPL'
)

generator.kinesis_client.put_record(
    StreamName='stock-trades-stream',
    Data=json.dumps(trade2),
    PartitionKey='AAPL'
)

print('UPSERT test trades sent!')
"
```

## 🔧 Monitoring and Management

### Monitor Pipeline Health
```bash
# Run the monitoring script
python3 monitor-kds-s3tables.py
```

### Query Data with Athena
```sql
-- Count total trades
SELECT COUNT(*) as total_trades FROM trading.trades;

-- View recent trades
SELECT 
    trade_id,
    stock_symbol,
    price,
    quantity,
    trade_type,
    timestamp
FROM trading.trades 
ORDER BY timestamp DESC 
LIMIT 10;

-- Check UPSERT results
SELECT * FROM trading.trades 
WHERE trade_id = 'UPSERT-TEST-001';

-- Analyze trading patterns
SELECT 
    stock_symbol,
    AVG(price) as avg_price,
    SUM(quantity) as total_volume,
    COUNT(*) as trade_count
FROM trading.trades 
WHERE timestamp >= current_timestamp - interval '1' hour
GROUP BY stock_symbol
ORDER BY total_volume DESC;
```

### Performance Tuning

#### Scaling Kinesis
```bash
# Increase shard count for higher throughput
aws kinesis update-shard-count \
    --stream-name stock-trades-stream \
    --target-shard-count 4 \
    --scaling-type UNIFORM_SCALING
```

#### Firehose Optimization
- Adjust buffer size (5MB) and interval (300s) based on data volume
- Monitor CloudWatch metrics for delivery success rates
- Configure appropriate retry settings

#### Lambda Optimization
- Increase memory allocation for trend analysis
- Adjust batch size for Kinesis event source
- Configure reserved concurrency if needed

## 🛠️ Troubleshooting

### Common Issues

#### 1. Firehose Delivery Failures
```bash
# Check Firehose logs
aws logs filter-log-events \
    --log-group-name /aws/kinesisfirehose/KDS-s3tables-trades \
    --start-time $(date -d '1 hour ago' +%s)000

# Common solutions:
# - Verify IAM permissions for FirehoseS3TablesRole
# - Check Lake Formation permissions
# - Ensure S3 Tables schema matches incoming data
```

#### 2. Lambda Function Errors
```bash
# Check Lambda logs for errors
aws logs tail /aws/lambda/StockTradingStack-TrendAnalyzerFunction* --since 1h

# Common issues:
# - Bedrock model access not enabled
# - Insufficient IAM permissions
# - Network connectivity issues
```

#### 3. S3 Tables Access Issues
```bash
# Verify table exists
aws s3tables get-table \
    --table-bucket-arn arn:aws:s3tables:us-east-1:ACCOUNT:bucket/stock-trading-demo \
    --namespace trading \
    --name trades

# Check Lake Formation permissions
aws lakeformation list-permissions \
    --principal DataLakePrincipalIdentifier=arn:aws:iam::ACCOUNT:role/FirehoseS3TablesRole
```

#### 4. Athena Query Failures
```bash
# Common Athena issues:
# - Table metadata not properly configured
# - Missing Iceberg metadata location
# - Insufficient permissions for query results bucket

# Fix table metadata
aws glue update-table \
    --database-name trading \
    --table-input file://updated-table-definition.json
```

#### 5. Live error → fix recipes (federation, LF, Bedrock)

The most common errors from a fresh deploy and their root causes — see
"Critical AWS Documentation Gaps" above for full context on each.

| Error message | Root cause | Fix |
|---|---|---|
| `MalformedFieldWithinRecord` (Firehose) | Producer emits `YYYY-MM-DD HH:MM:SS` instead of ISO 8601 | Switch producer to `datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]` |
| `The IAM role configured doesn't have access to write to S3 Tables` (Firehose at create time) | IAM eventual consistency — Firehose validates the role before it has propagated | Re-run `cdk deploy` 30s later, or use `--context skipFirehose=true` two-phase |
| `s3tables:GetTableBucket on resource: ...:bucket/stock-trading-demo` | Federation calls `GetTableBucket`/`GetNamespace` which weren't in the policy | Add both actions plus `bucket/<name>` (no wildcard) resource |
| `Invalid catalog specified <account>:s3tablescatalog/stock-trading-demo` (Athena) | Federated catalog not registered as Athena DataCatalog | Create `athena.CfnDataCatalog` with `type=GLUE` and `parameters.catalog-id=<account>:s3tablescatalog/<bucket>` |
| `SCHEMA_NOT_FOUND: Schema 'trading' does not exist` (Athena) | Query ran against `AwsDataCatalog` instead of the federated catalog | Pass `Catalog: s3tables_stock_trading` in `QueryExecutionContext` |
| `Insufficient Lake Formation permission(s): Required Describe on trading` | LF gate not granted to the principal | Issue out-of-band CLI grants (CFN can't model federated catalog ids) |
| `ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't supported` | Claude Haiku 4.5 is not invokable as a foundation model | Use `us.anthropic.claude-haiku-4-5-20251001-v1:0` inference profile + grant access to both ARNs |
| Firehose appears stuck (no data in S3 Tables, no errors) | Buffer hasn't flushed yet | Wait up to 5 min, or push 500+ records to trigger size flush |
| CW dashboard shows `DeliveryToS3.Success = 0` for Iceberg destination | Wrong metric namespace | Use `DeliveryToIceberg.SuccessfulRowCount` (dots, not slashes) |


### Debug Mode
```bash
# Enable verbose logging
export AWS_CLI_FILE_ENCODING=UTF-8
export CDK_DEBUG=true

# Deploy with debug output
cdk deploy --verbose
```

## 🧹 Cleanup

### Remove All Resources
```bash
./teardown.sh
```

`teardown.sh` runs `cdk destroy` (which removes the Firehose, IAM role,
Glue DB+Table, Lake Formation grants, Lambda, dashboard, alarms, and DLQ)
then prompts before removing the S3 Tables namespace, table, and bucket.

## 📚 Key Components

### Data Flow Architecture
1. **Trade Generator** → Produces simulated stock trades
2. **Kinesis Data Stream** → Buffers and distributes trade data
3. **Kinesis Data Firehose** → Processes and delivers data to S3 Tables
4. **S3 Tables** → Stores data in Apache Iceberg format with ACID transactions
5. **Lambda Function** → Analyzes trends using Amazon Bedrock
6. **SNS** → Sends notifications for detected trends

### UPSERT Operations
- **Unique Key**: `trade_id` ensures data consistency
- **ACID Transactions**: Apache Iceberg provides full transaction support
- **Real-time Updates**: Trade corrections and updates supported
- **Schema Evolution**: Table schema can evolve without downtime

### Monitoring and Observability
- **CloudWatch Logs**: Comprehensive logging for all components
- **CloudWatch Metrics**: Performance monitoring and alerting
- **Athena Queries**: SQL-based data analysis and reporting
- **SNS Notifications**: Real-time alerts for business events

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues and questions:
1. Check the troubleshooting section above
2. Review AWS service documentation
3. Check CloudWatch logs for detailed error messages
4. Ensure all prerequisites are met

---

**Note**: This solution demonstrates streaming data processing concepts and should be adapted for production use with appropriate security, monitoring, and error handling considerations.
