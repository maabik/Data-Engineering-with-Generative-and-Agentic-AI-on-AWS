# iceberg-time-travel-debugger

CLI tool that diffs two Apache Iceberg snapshots and produces a **row-level changelog** — answering questions like *"what changed between yesterday 5pm and now?"* using Athena's `FOR VERSION AS OF` time-travel syntax.

## What It Does

Given an Iceberg table and a time range, the debugger:

1. Queries the Iceberg snapshot history (`$snapshots` metadata table) to find the closest snapshot at or before each timestamp
2. Runs two Athena `EXCEPT` queries using `FOR VERSION AS OF <snapshot_id>` to identify rows that appeared or disappeared between the snapshots
3. Prints a changelog — **ADDED** rows (exist in the later snapshot but not the earlier) and **REMOVED** rows (exist in the earlier snapshot but not the later)

Unchanged rows are not returned — only genuine additions and deletions appear in the diff.

## Architecture

```
cli/debugger.py
  |
  |-- parse_natural_time()        # "yesterday 5pm" -> UTC datetime
  |-- find_snapshot_at()          # Athena: SELECT from <table>$snapshots
  |-- diff_snapshots()            # Athena: SELECT ... FOR VERSION AS OF <id>
                                  #         EXCEPT
                                  #         SELECT ... FOR VERSION AS OF <id>
  |-- format_table/json/csv()     # Output formatting
  v
Amazon Athena (engine v3, iceberg-debugger workgroup)
  |
  v
AWS Glue Data Catalog (iceberg_debugger database)
  |
  v
S3 (Iceberg table data: s3://BUCKET/iceberg/permits/)
```

## Project Layout

```
iceberg-time-travel-debugger/
├── cli/
│   ├── debugger.py          # CLI entrypoint
│   └── requirements.txt
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── iceberg_debugger_stack.py
├── lambda/
│   └── seed_iceberg.py      # creates Iceberg table + seeds snapshots via Athena
└── README.md
```

## Prerequisites

- AWS CLI configured
- CDK bootstrapped (`cdk bootstrap`)
- Python 3.9+

## Deploy

```bash
cd cdk
python3 -m venv .venv && source .venv/bin/activate
pip install aws-cdk-lib constructs
cdk deploy
```

## Setup: Lake Formation Admin

Grant the CDK execution role, seed Lambda role, and your own admin role Lake Formation data lake admin privileges. Replace `ACCOUNT` and `REGION`:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
SEED_ROLE=$(aws cloudformation describe-stack-resources \
  --stack-name IcebergDebuggerStack \
  --query 'StackResources[?contains(LogicalResourceId,`SeedLambdaRole`)].PhysicalResourceId' \
  --output text)

aws lakeformation put-data-lake-settings --data-lake-settings "{
  \"DataLakeAdmins\": [
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/cdk-hnb659fds-cfn-exec-role-${ACCT}-us-west-2\"},
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/${SEED_ROLE}\"},
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/Admin\"}
  ],
  \"CreateTableDefaultPermissions\": [],
  \"CreateDatabaseDefaultPermissions\": []
}"
```

Then grant the seed Lambda role LF database/table permissions:

```bash
SEED_ROLE_ARN="arn:aws:iam::${ACCT}:role/${SEED_ROLE}"

aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"${SEED_ROLE_ARN}\"}" \
  --resource "{\"Database\": {\"CatalogId\": \"${ACCT}\", \"Name\": \"iceberg_debugger\"}}" \
  --permissions "CREATE_TABLE" "DESCRIBE" "ALTER"

aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"${SEED_ROLE_ARN}\"}" \
  --resource "{\"Table\": {\"CatalogId\": \"${ACCT}\", \"DatabaseName\": \"iceberg_debugger\", \"TableWildcard\": {}}}" \
  --permissions "ALL"
```

Grant your own principal SELECT on the permits table (for CLI queries):

```bash
MY_ROLE_ARN=$(aws sts get-caller-identity --query Arn --output text | sed 's|:sts:|:iam:|; s|assumed-role/\([^/]*\)/.*|role/\1|')

aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"${MY_ROLE_ARN}\"}" \
  --resource "{\"Table\": {\"CatalogId\": \"${ACCT}\", \"DatabaseName\": \"iceberg_debugger\", \"Name\": \"permits\"}}" \
  --permissions "SELECT" "DESCRIBE"
```

## Setup: Seed the Iceberg Table

After deploy, invoke the seed Lambda to create the Iceberg table and populate it with two batches of rows (two distinct snapshots):

```bash
SEED_FN=$(aws cloudformation describe-stacks \
  --stack-name IcebergDebuggerStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SeedFunctionName`].OutputValue' \
  --output text)

aws lambda invoke --function-name "$SEED_FN" --payload '{}' \
  --cli-binary-format raw-in-base64-out /tmp/seed.json && cat /tmp/seed.json
```

Expected output includes `snapshots` — two entries with `snapshot_id` and `committed_at`.

## CLI Usage

Install dependencies:

```bash
cd cli
pip install -r requirements.txt
```

### List Available Snapshots

```bash
python debugger.py \
  --table iceberg_debugger.permits \
  --workgroup iceberg-debugger \
  --list-snapshots
```

### Diff with Natural-Language Time Range

```bash
python debugger.py \
  --table iceberg_debugger.permits \
  --from "yesterday 5pm" --to "now" \
  --workgroup iceberg-debugger
```

### Diff with Explicit Timestamps

```bash
python debugger.py \
  --table iceberg_debugger.permits \
  --from "2024-01-15 00:00" --to "2024-02-10 00:00" \
  --workgroup iceberg-debugger \
  --format json
```

### Output as CSV

```bash
python debugger.py \
  --table iceberg_debugger.permits \
  --from "2024-01-01" --to "now" \
  --workgroup iceberg-debugger \
  --format csv > changelog.csv
```

### Supported `--from` / `--to` Expressions

| Expression | Meaning |
|---|---|
| `now` | Current UTC time |
| `yesterday 5pm` | Yesterday at 17:00 UTC |
| `yesterday` | Yesterday at midnight UTC |
| `today 9am` | Today at 09:00 UTC |
| `3 hours ago` | 3 hours before now |
| `2 days ago` | 2 days before now |
| `2024-03-15` | Date at midnight UTC |
| `2024-03-15 17:00` | Explicit date and time |
| `2024-03-15T17:00:00` | ISO 8601 |

## How Iceberg Time Travel Works

Iceberg stores immutable snapshots. Each `INSERT`, `DELETE`, or `UPDATE` operation creates a new snapshot in the table's metadata. Athena supports querying a specific snapshot with:

```sql
SELECT * FROM "db"."table" FOR VERSION AS OF <snapshot_id>
```

The debugger uses `EXCEPT` to find the symmetric difference between two snapshots:

```sql
-- Rows added between snap_from and snap_to
SELECT * FROM table FOR VERSION AS OF {snap_to}
EXCEPT
SELECT * FROM table FOR VERSION AS OF {snap_from}

-- Rows removed between snap_from and snap_to
SELECT * FROM table FOR VERSION AS OF {snap_from}
EXCEPT
SELECT * FROM table FOR VERSION AS OF {snap_to}
```

`EXCEPT` returns rows that appear in the first query but not the second — giving exact row-level additions and deletions with no false positives for unchanged rows.

## Sample Output

```
Table:    iceberg_debugger.permits
From:     2024-01-14T00:00:00+00:00
To:       2024-02-10T00:00:00+00:00

Looking up snapshots...
Snapshot (from): 3821049823091234567
Snapshot (to):   4912038471029384756

Diffing snapshots via Athena...

Changelog: 3 added, 0 removed

============================================================
  ADDED (3 rows)
============================================================
+------+----------------+-------------+---------+------------+---------+
| permit_id | address   | permit_type | status  | issued_date| fee_usd |
+------+----------------+-------------+---------+------------+---------+
| P006 | 999 Cedar Ln   | BUILDING    | ISSUED  | 2024-02-01 | 300.0   |
| P007 | 888 Birch Blvd | ELECTRICAL  | PENDING | 2024-02-03 |  90.0   |
| P008 | 777 Walnut Way | BUILDING    | ISSUED  | 2024-02-05 | 420.0   |
+------+----------------+-------------+---------+------------+---------+
```

## IAM Permissions

The CLI requires the `IcebergDebuggerCliPolicy` managed policy. Attach it to your IAM user or role:

```bash
POLICY_ARN=$(aws cloudformation describe-stacks \
  --stack-name IcebergDebuggerStack \
  --query 'Stacks[0].Outputs[?OutputKey==`CliPolicyArn`].OutputValue' \
  --output text)

aws iam attach-user-policy \
  --user-name YOUR_USER \
  --policy-arn "$POLICY_ARN"
```

## Tear Down

```bash
cdk destroy
```

The S3 bucket uses `RemovalPolicy.DESTROY` with auto-delete — fully cleaned up on stack deletion.
