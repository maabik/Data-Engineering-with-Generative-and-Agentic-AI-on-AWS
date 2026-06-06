# lakeformation-data-filters

Demonstrates AWS Lake Formation fine-grained access control using **data filters** — the mechanism described in *Data Engineering with AWS and Generative AI* Chapter 3. A single `cdk deploy` provisions column-level and row-level security on an IMDB-style dataset, with two simulated analyst personas that each see a restricted view of the same table.

## What are Data Filters?

Data filters are Lake Formation objects that attach to a specific Glue table and define:

- **Column-level access** — which columns a principal is allowed to see (include or exclude)
- **Row-level access** — a PartiQL filter expression applied to every query, restricting which rows are returned

A principal (IAM role or user) is granted SELECT on a data filter rather than on the table directly. Lake Formation enforces the filter transparently across Athena, EMR, Glue ETL, and Redshift Spectrum — no application-layer filtering required.

## Architecture

```
S3 bucket
  imdb/principals/principals.csv
         │
         ▼ (Glue Crawler: imdb-principals-crawler)
Glue Database: imdb
  Table: principals
    columns: tconst | nconst | category | job | characters
         │
         ├── Data Filter: analysts-restricted
         │     columns:  tconst, nconst, category
         │     rows:     category = 'actor' OR category = 'actress'
         │     granted → AnalystRole
         │
         └── Data Filter: writers-view
               columns:  tconst, nconst, category, job
               rows:     category = 'writer'
               granted → WriterResearcherRole

Lambda: seed_imdb.py  →  writes 25-row IMDB principals CSV to S3
```

## Data Filters

### `analysts-restricted`

Mirrors the Chapter 3 example: an analyst researching performers needs only three columns and must not see writers or directors.

| Property | Value |
|---|---|
| Columns | `tconst`, `nconst`, `category` |
| Row filter | `category='actor' OR category='actress'` |
| Granted to | `AnalystRole` |

### `writers-view`

A writer researcher needs job metadata but must only see writing credits.

| Property | Value |
|---|---|
| Columns | `tconst`, `nconst`, `category`, `job` |
| Row filter | `category='writer'` |
| Granted to | `WriterResearcherRole` |

## Project Layout

```
lakeformation-data-filters/
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── lakeformation_filters_stack.py
├── lambda/
│   ├── seed_imdb.py       # seeds IMDB principals CSV to S3
│   └── requirements.txt
└── scripts/
    └── create_filters_boto3.py   # imperative boto3 equivalent of the
                                  # CDK filter creation (Chapter 3, p. 124)
```

## Programmatic creation with boto3

The CDK stack creates the data filters declaratively. For workflows where
the catalog table is already deployed and you want to add a new persona's
filter at runtime, `scripts/create_filters_boto3.py` shows the same shapes
via `lakeformation.create_data_cells_filter()`:

```bash
# Create both filters against an existing imdb.principals table
python scripts/create_filters_boto3.py --replace

# Also grant SELECT on each filter to its persona role
python scripts/create_filters_boto3.py \
    --analyst-role-arn arn:aws:iam::111122223333:role/AnalystRole \
    --writer-role-arn  arn:aws:iam::111122223333:role/WriterResearcherRole
```

Caller must be a Lake Formation data lake admin (or hold DESCRIBE on the
target table) and have `lakeformation:CreateDataCellsFilter` /
`lakeformation:GrantPermissions`.

## Prerequisites

- AWS CLI configured
- CDK bootstrapped (`cdk bootstrap`)
- Your IAM role/user set as Lake Formation data lake admin (see Setup)

## Setup

Grant your CDK execution role and admin role Lake Formation admin status:

```bash
aws lakeformation put-data-lake-settings --data-lake-settings '{
  "DataLakeAdmins": [
    {"DataLakePrincipalIdentifier": "arn:aws:iam::ACCOUNT:role/cdk-hnb659fds-cfn-exec-role-ACCOUNT-REGION"},
    {"DataLakePrincipalIdentifier": "arn:aws:iam::ACCOUNT:role/YourAdminRole"}
  ],
  "CreateTableDefaultPermissions": [],
  "CreateDatabaseDefaultPermissions": []
}'
```

## Deploy

```bash
cd cdk
python3 -m venv .venv && source .venv/bin/activate
pip install aws-cdk-lib constructs
cdk bootstrap   # first time only
cdk deploy
```

## Testing

### 1. Seed the dataset

```bash
SEED_FN=$(aws cloudformation describe-stacks \
  --stack-name LakeFormationFiltersStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SeedFunctionName`].OutputValue' \
  --output text)

aws lambda invoke --function-name "$SEED_FN" --payload '{}' \
  --cli-binary-format raw-in-base64-out /tmp/seed.json && cat /tmp/seed.json
```

Expected: `{"statusCode": 200, "rows": 25, "s3_key": "imdb/principals/principals.csv"}`

### 2. Run the Glue crawler

```bash
aws glue start-crawler --name imdb-principals-crawler

# Poll until READY
watch -n 5 'aws glue get-crawler --name imdb-principals-crawler \
  --query "Crawler.{State:State,Status:LastCrawl.Status}" --output table'
```

### 3. Verify data filters

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)

aws lakeformation list-data-cells-filter \
  --table "{\"CatalogId\":\"${ACCT}\",\"DatabaseName\":\"imdb\",\"Name\":\"principals\"}" \
  --query 'DataCellsFilters[].{Name:Name,Columns:ColumnNames,RowFilter:RowFilter.FilterExpression}' \
  --output table
```

Expected output:
```
analysts-restricted | [tconst, nconst, category]        | category='actor' OR category='actress'
writers-view        | [tconst, nconst, category, job]   | category='writer'
```

### 4. Verify grants

```bash
# Grants on the analyst filter
ANALYST_ARN=$(aws cloudformation describe-stacks \
  --stack-name LakeFormationFiltersStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AnalystRoleArn`].OutputValue' \
  --output text)

aws lakeformation list-permissions \
  --principal "DataLakePrincipalIdentifier=${ANALYST_ARN}" \
  --query 'PrincipalResourcePermissions[].{Resource:Resource,Permissions:Permissions}' \
  --output json
```

### 5. Query with Athena as each persona

```sql
-- As AnalystRole: sees only tconst, nconst, category and only actors/actresses
SELECT * FROM "imdb"."principals" LIMIT 10;
-- Returns: 3 columns, rows where category IN ('actor','actress') only

-- As WriterResearcherRole: sees tconst, nconst, category, job and only writers
SELECT * FROM "imdb"."principals" LIMIT 10;
-- Returns: 4 columns, rows where category = 'writer' only
```

To query as a specific role in Athena, assume the role first:

```bash
# Get temporary credentials for the analyst role
ANALYST_ARN=$(aws cloudformation describe-stacks \
  --stack-name LakeFormationFiltersStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AnalystRoleArn`].OutputValue' \
  --output text)

CREDS=$(aws sts assume-role --role-arn "$ANALYST_ARN" \
  --role-session-name analyst-test --output json)

export AWS_ACCESS_KEY_ID=$(echo $CREDS | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
export AWS_SECRET_ACCESS_KEY=$(echo $CREDS | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
export AWS_SESSION_TOKEN=$(echo $CREDS | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SessionToken'])")

# Now run Athena queries — Lake Formation enforces the analysts-restricted filter
```

## IAM Permissions Summary

| Principal | IAM permissions | Lake Formation permissions |
|---|---|---|
| Crawler role | `AWSGlueServiceRole` + S3 read | `CREATE_TABLE`, `DESCRIBE`, `ALTER` on `imdb` db; `ALL` on tables |
| Seed Lambda role | `AWSLambdaBasicExecutionRole` + `s3:PutObject` | — |
| AnalystRole | `AmazonAthenaFullAccess` + S3 read | `SELECT` via `analysts-restricted` filter |
| WriterResearcherRole | `AmazonAthenaFullAccess` + S3 read | `SELECT` via `writers-view` filter |

## How Data Filters Work

```
Query: SELECT * FROM imdb.principals

Without filter:                    With analysts-restricted filter:
┌────────┬────────┬──────────┬──────────┬────────────┐    ┌────────┬────────┬──────────┐
│ tconst │ nconst │ category │ job      │ characters │    │ tconst │ nconst │ category │
├────────┼────────┼──────────┼──────────┼────────────┤    ├────────┼────────┼──────────┤
│ tt0001 │ nm0001 │ actor    │ self     │ [...]      │    │ tt0001 │ nm0001 │ actor    │
│ tt0001 │ nm0002 │ director │ null     │ \N         │ ←  │ tt0003 │ nm0003 │ actress  │
│ tt0003 │ nm0003 │ actress  │ self     │ [...]      │    └────────┴────────┴──────────┘
│ tt0007 │ nm0042 │ writer   │ screenplay│ \N        │    (director + writer rows hidden,
└────────┴────────┴──────────┴──────────┴────────────┘     job + characters columns hidden)
```

Lake Formation intercepts the query before it reaches S3 and rewrites it to apply both the column projection and the row predicate. The analyst never sees the restricted rows or columns, and no data is duplicated or copied.

## Tear Down

```bash
cdk destroy
```

The data bucket uses `RemovalPolicy.DESTROY` with auto-delete objects — it is fully removed on stack deletion.
