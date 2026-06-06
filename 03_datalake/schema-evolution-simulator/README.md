# schema-evolution-simulator

A chaos-engineering learning tool that runs a catalog of **safe**, **unsafe**, and **chaos** Iceberg schema mutations against a live Athena/Glue table, validates downstream consumer behavior, and produces a detailed results report.

## What It Does

The simulator runs 12 pre-defined schema mutations in sequence, each against a freshly reset baseline table:

1. Executes the mutation DDL via Athena
2. Runs a validation query to observe the outcome
3. Compares actual behavior against expected behavior
4. Reports the result with per-consumer notes (Athena, Redshift Spectrum, Glue ETL)

Each mutation teaches something about Iceberg's schema evolution guarantees (and gaps).

## Mutation Catalog

### Safe Mutations — Iceberg spec guarantees backward compatibility

| Name | What happens |
|---|---|
| `add_nullable_column` | Adds a nullable column; old files return NULL for it |
| `rename_column` | Renames via field ID mapping; no Parquet rewrite |
| `reorder_columns` | Metadata-only; SELECT * returns new catalog order |
| `drop_column` | Tombstones the field ID; data lingers until compaction |
| `widen_int_to_bigint` | INT→BIGINT is an Iceberg-allowed type promotion |

### Unsafe Mutations — Allowed by Iceberg but operationally risky

| Name | What happens |
|---|---|
| `add_column_with_default` | Athena engine v3 rejects `DEFAULT` in `ADD COLUMNS` — engine gap |
| `drop_referenced_column` | DDL succeeds; downstream queries that reference the column break |

### Chaos Mutations — Deliberately provoke failures or silent corruption

| Name | What happens |
|---|---|
| `add_not_null_column` | Athena rejects `NOT NULL` in `ADD COLUMNS` at parse time |
| `narrow_type_bigint_to_int` | **Silently accepted by Athena** despite being off-spec — data loss risk for values > INT_MAX |
| `incompatible_type_change` | STRING→DATE rejected with type-incompatibility error |
| `rename_to_existing_name` | Name collision rejected by engine |
| `drop_partition_column` | Rejected — Iceberg disallows dropping active partition fields |

### Key Findings from Live Runs

- **`narrow_type_bigint_to_int`**: Athena engine v3 **accepts** BIGINT→INT demotion without error. Values within INT range survive; values > 2,147,483,647 would be silently truncated. This is non-spec behavior and a silent data corruption hazard.
- **`add_column_with_default`**: Iceberg v2 supports column defaults, but Athena engine v3 does not implement `DEFAULT` in `ALTER TABLE ADD COLUMNS`. Workaround: add nullable, then backfill.
- **`add_not_null_column`**: Both `NOT NULL` and `DEFAULT` are rejected at parse time by Athena. Constraint enforcement must happen at the application or DQ layer.

## Architecture

```
simulator/simulator.py
  |
  |-- reset_table()         # DROP + CREATE + INSERT to baseline schema
  |-- run_mutation()        # Execute DDL, run validation query, record result
  |-- MutationResult        # verdict: PASS | DDL_UNEXPECTED | VALIDATION_UNEXPECTED
  v
Amazon Athena (iceberg-schema-sim workgroup, engine v3)
  |
  v
AWS Glue Data Catalog (iceberg_schema_sim database)
  |
  v
S3 (Iceberg table: s3://BUCKET/iceberg/orders/)
```

## Project Layout

```
schema-evolution-simulator/
├── simulator/
│   ├── simulator.py        # CLI entrypoint and runner
│   ├── mutations.py        # Mutation catalog (12 mutations)
│   └── requirements.txt
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── schema_sim_stack.py
├── lambda/
│   └── seed_table.py       # Creates and seeds the baseline Iceberg table
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

Grant the CDK execution role, seed Lambda role, and your admin role LF data lake admin privileges:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
SEED_ROLE=$(aws cloudformation describe-stack-resources \
  --stack-name SchemaSimStack \
  --query 'StackResources[?LogicalResourceId==`SeedLambdaRole88DBA302`].PhysicalResourceId' \
  --output text)

aws lakeformation put-data-lake-settings --data-lake-settings "{
  \"DataLakeAdmins\": [
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/cdk-hnb659fds-cfn-exec-role-${ACCT}-REGION\"},
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/${SEED_ROLE}\"},
    {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/Admin\"}
  ],
  \"CreateTableDefaultPermissions\": [],
  \"CreateDatabaseDefaultPermissions\": []
}"

# Grant seed role DB + table permissions
aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/${SEED_ROLE}\"}" \
  --resource "{\"Database\": {\"CatalogId\": \"${ACCT}\", \"Name\": \"iceberg_schema_sim\"}}" \
  --permissions "CREATE_TABLE" "DESCRIBE" "ALTER"

aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/${SEED_ROLE}\"}" \
  --resource "{\"Table\": {\"CatalogId\": \"${ACCT}\", \"DatabaseName\": \"iceberg_schema_sim\", \"TableWildcard\": {}}}" \
  --permissions "ALL"

# Grant your admin role ALL on the database and tables
aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/Admin\"}" \
  --resource "{\"Database\": {\"CatalogId\": \"${ACCT}\", \"Name\": \"iceberg_schema_sim\"}}" \
  --permissions "CREATE_TABLE" "DESCRIBE" "ALTER"

aws lakeformation grant-permissions \
  --principal "{\"DataLakePrincipalIdentifier\": \"arn:aws:iam::${ACCT}:role/Admin\"}" \
  --resource "{\"Table\": {\"CatalogId\": \"${ACCT}\", \"DatabaseName\": \"iceberg_schema_sim\", \"TableWildcard\": {}}}" \
  --permissions "ALL"
```

**Important**: Do NOT register the S3 bucket with Lake Formation. Leave it as IAM-managed so Athena's Iceberg writer can access it directly.

## Setup: Seed the Table

```bash
SEED_FN=$(aws cloudformation describe-stacks \
  --stack-name SchemaSimStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SeedFunctionName`].OutputValue' \
  --output text)

aws lambda invoke --function-name "$SEED_FN" --payload '{}' \
  --cli-binary-format raw-in-base64-out /tmp/seed.json && cat /tmp/seed.json
```

Expected: `{"statusCode": 200, "table": "iceberg_schema_sim.orders", "rows": 8}`

## CLI Usage

```bash
cd simulator
pip install -r requirements.txt
```

### Run all mutations

```bash
python simulator.py --table iceberg_schema_sim.orders --workgroup iceberg-schema-sim
```

### Run only safe mutations

```bash
python simulator.py --table iceberg_schema_sim.orders --run safe
```

### Run only chaos mutations

```bash
python simulator.py --table iceberg_schema_sim.orders --run chaos
```

### Run a single named mutation

```bash
python simulator.py --table iceberg_schema_sim.orders --run narrow_type_bigint_to_int
```

### List all available mutations

```bash
python simulator.py --list
```

### Explain a mutation in detail

```bash
python simulator.py --explain narrow_type_bigint_to_int
```

### Dry-run (print DDL without executing)

```bash
python simulator.py --table iceberg_schema_sim.orders --run all --dry-run
```

### JSON output

```bash
python simulator.py --table iceberg_schema_sim.orders --format json > results.json
```

## Sample Output

```
Schema Evolution Simulator
Table:     iceberg_schema_sim.orders
Workgroup: iceberg-schema-sim
Mutations: 12 selected (all)

[1/12] SAFE   add_nullable_column
         ✓ PASS (DDL=OK, rows=5, 4.8s)
[2/12] SAFE   rename_column
         ✓ PASS (DDL=OK, rows=5, 4.9s)
...
[9/12] CHAOS  narrow_type_bigint_to_int
         ✓ PASS (DDL=OK, rows=5, 11.2s)

Results: 12/12 mutations behaved as expected
```

## Verdict Logic

| Verdict | Meaning |
|---|---|
| `PASS` | DDL outcome and validation matched expectations |
| `DDL_UNEXPECTED` | DDL succeeded when expected to fail, or failed when expected to succeed |
| `VALIDATION_UNEXPECTED` | DDL was correct but validation returned unexpected row count |

For chaos mutations where `expect_rows=None`, the validation result is always observed (never fails the verdict) — the interesting information is in the DDL outcome and the consumer notes.

## Extending the Simulator

Add new mutations to `mutations.py` following the existing dict structure. The `expect_ddl_ok` and `expect_rows` fields drive the pass/fail verdict. Set `expect_rows=None` for chaos mutations where you just want to observe behavior without asserting an expected count.

## Tear Down

```bash
cdk destroy
```

The S3 bucket uses `RemovalPolicy.DESTROY` — fully cleaned up on stack deletion.
