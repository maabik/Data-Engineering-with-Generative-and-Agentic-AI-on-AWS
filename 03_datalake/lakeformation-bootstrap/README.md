# lakeformation-bootstrap

A turnkey **AWS Lake Formation data lake** with all four "fancy" fine-grained
controls referenced in Chapter 3 of *Data Engineering with Generative and
Agentic AI on AWS*. One `cdk deploy` provisions the bucket, the catalog, the
governance, three personas with different views of the same table, and an
optional cross-account share.

## What you get

| Control | What it demonstrates | Demo persona |
|---|---|---|
| **LF-Tag-based access control (LF-TBAC)** | Tag tables and columns by `classification` and `department`, grant by tag expression | `TaggedAnalystRole` |
| **Column-level filter** | Hide PII columns (`owner_email`, `owner_phone`) from a principal | `NoPiiAnalystRole` |
| **Row-level filter** | Restrict rows by a PartiQL predicate (`violation_district='SOUTH'`) | `SouthDistrictRole` |
| **Cross-account share** | Share the database with another AWS account via LF + RAM (optional) | external account ID you supply |

All four controls are enforced by Lake Formation across Athena, Glue ETL,
EMR, and Redshift Spectrum — no application-layer filtering required.

## Architecture

```
S3 bucket (data lake)
  chicago_violations/violations.csv (25 sample rows)
        │
        ▼ (LF data location registration)
Lake Formation
        │
        ▼
Glue Database: chicago_violations
  Table: violations
    columns: id, docket_number, address, violation_district, category,
             imposed_fine, owner_email, owner_phone, violation_date

LF-Tags:
  classification = {public, internal, confidential}
  department     = {finance, operations, public_safety}

Tag assignments:
  database chicago_violations             -> department=public_safety
  columns id, docket_number, address,     -> classification=internal
          violation_district, category,
          imposed_fine, violation_date
  columns owner_email, owner_phone        -> classification=confidential

Data filters:
  analyst-no-pii         columns: 7 non-PII columns / all rows
  south-district-only    columns: 7 non-PII columns / violation_district='SOUTH'

Grants:
  TaggedAnalystRole  -> SELECT on tables WHERE classification=internal
                                         AND department=public_safety
  NoPiiAnalystRole   -> SELECT via analyst-no-pii filter
  SouthDistrictRole  -> SELECT via south-district-only filter
  consumerAccountId  -> SELECT,DESCRIBE on the database (if context set)
```

## Project Layout

```
lakeformation-bootstrap/
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   ├── requirements.txt
│   └── stacks/
│       └── lakeformation_bootstrap_stack.py
├── lambda/
│   ├── seed_data.py            # writes 25-row Chicago violations CSV
│   └── requirements.txt
└── scripts/
    ├── teardown_producer.sh    # cdk destroy + remove Athena results bucket
    └── teardown_consumer.sh    # remove cross-account artifacts in consumer
```

## Prerequisites

- AWS CLI configured for the producer account
- CDK v2 installed (`npm install -g aws-cdk`) and bootstrapped
  (`cdk bootstrap`)
- Python 3.10+
- **Your IAM identity must already be a Lake Formation administrator** in
  the target region — the stack adds the CFN execution role as an admin,
  but you must be one yourself to deploy without errors. Set yourself as
  one in *Lake Formation console -> Administrative roles and tasks*, or:

  ```bash
  ACCT=$(aws sts get-caller-identity --query Account --output text)
  ME=$(aws sts get-caller-identity --query Arn --output text)

  aws lakeformation put-data-lake-settings --data-lake-settings "{
    \"DataLakeAdmins\": [
      {\"DataLakePrincipalIdentifier\": \"$ME\"},
      {\"DataLakePrincipalIdentifier\": \"arn:aws:iam::$ACCT:role/cdk-hnb659fds-cfn-exec-role-$ACCT-$(aws configure get region)\"}
    ],
    \"CreateTableDefaultPermissions\": [],
    \"CreateDatabaseDefaultPermissions\": []
  }"
  ```

## Deploy

```bash
cd cdk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk deploy
```

To enable the **cross-account share**, pass a consumer account ID via context:

```bash
cdk deploy -c consumerAccountId=123456789012
```

The consumer account receives **two RAM invitations** (one per LF-managed
share) and **must accept them** unless RAM has been configured to trust the
AWS Organization end-to-end:

```bash
# In the consumer account
aws ram get-resource-share-invitations \
  --query 'resourceShareInvitations[?status==`PENDING`].resourceShareInvitationArn' \
  --output text \
  | xargs -n1 -I {} aws ram accept-resource-share-invitation --resource-share-invitation-arn {}
```

After acceptance, the consumer account also needs to:

1. **Designate a Lake Formation admin** in the consumer account (a one-time
   step — LF starts with no admins by default):
   ```bash
   ME=$(aws sts get-caller-identity --query Arn --output text)
   aws lakeformation put-data-lake-settings --data-lake-settings "{
     \"DataLakeAdmins\": [{\"DataLakePrincipalIdentifier\": \"$ME\"}],
     \"CreateDatabaseDefaultPermissions\": [{\"Principal\":{\"DataLakePrincipalIdentifier\":\"IAM_ALLOWED_PRINCIPALS\"},\"Permissions\":[\"ALL\"]}],
     \"CreateTableDefaultPermissions\": [{\"Principal\":{\"DataLakePrincipalIdentifier\":\"IAM_ALLOWED_PRINCIPALS\"},\"Permissions\":[\"ALL\"]}],
     \"Parameters\": {\"CROSS_ACCOUNT_VERSION\": \"4\", \"SET_CONTEXT\": \"TRUE\"}
   }"
   ```

2. **Create a resource link** (a local pointer to the producer's database):
   ```bash
   PRODUCER_ACCT=<producer-account-id>
   aws glue create-database --database-input "{
     \"Name\": \"chicago_violations_link\",
     \"TargetDatabase\": {
       \"CatalogId\": \"$PRODUCER_ACCT\",
       \"DatabaseName\": \"chicago_violations\"
     }
   }"
   ```

3. **Re-grant LF permissions to a consumer-local IAM role**, since LF
   account-level grants don't auto-propagate to specific principals:
   ```bash
   ROLE_ARN=arn:aws:iam::<consumer-acct>:role/<your-analyst-role>
   aws lakeformation grant-permissions \
     --principal "DataLakePrincipalIdentifier=$ROLE_ARN" \
     --resource '{"Database":{"Name":"chicago_violations_link"}}' \
     --permissions DESCRIBE
   aws lakeformation grant-permissions \
     --principal "DataLakePrincipalIdentifier=$ROLE_ARN" \
     --resource "{\"Table\":{\"CatalogId\":\"$PRODUCER_ACCT\",\"DatabaseName\":\"chicago_violations\",\"Name\":\"violations\"}}" \
     --permissions SELECT
   ```

4. **Query through the resource link** in Athena:
   ```sql
   SELECT * FROM chicago_violations_link.violations LIMIT 5;
   ```

> **Note:** The cross-account grant in this stack does **not** apply LF data
> filters or tag policies — the consumer sees all columns and rows the
> producer chose to share. To restrict what the consumer sees, attach a
> data filter to the cross-account grant (replace the `Table` resource with
> a `DataCellsFilter` resource in the producer-side `CfnPrincipalPermissions`).

## Seed the dataset

```bash
SEED_FN=$(aws cloudformation describe-stacks \
  --stack-name LakeFormationBootstrapStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SeedFunctionName`].OutputValue' \
  --output text)

aws lambda invoke --function-name "$SEED_FN" --payload '{}' \
  --cli-binary-format raw-in-base64-out /tmp/seed.json && cat /tmp/seed.json
```

Expected: `{"statusCode": 200, "rows": 25, "s3_key": "chicago_violations/violations.csv"}`

## Verify each control

### Helper: assume a role and run an Athena query

```bash
assume() {
  local role_arn=$1
  local creds=$(aws sts assume-role --role-arn "$role_arn" \
    --role-session-name lf-demo --output json)
  export AWS_ACCESS_KEY_ID=$(echo "$creds" | python3 -c "import json,sys;print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
  export AWS_SECRET_ACCESS_KEY=$(echo "$creds" | python3 -c "import json,sys;print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
  export AWS_SESSION_TOKEN=$(echo "$creds" | python3 -c "import json,sys;print(json.load(sys.stdin)['Credentials']['SessionToken'])")
}

unassume() {
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
}
```

Get the role ARNs:

```bash
ARN_TAGGED=$(aws cloudformation describe-stacks --stack-name LakeFormationBootstrapStack \
  --query 'Stacks[0].Outputs[?OutputKey==`TaggedAnalystRoleArn`].OutputValue' --output text)
ARN_NOPII=$(aws cloudformation describe-stacks --stack-name LakeFormationBootstrapStack \
  --query 'Stacks[0].Outputs[?OutputKey==`NoPiiAnalystRoleArn`].OutputValue' --output text)
ARN_SOUTH=$(aws cloudformation describe-stacks --stack-name LakeFormationBootstrapStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SouthDistrictRoleArn`].OutputValue' --output text)
```

### #1: LF-TBAC — `TaggedAnalystRole`

This role is granted access by **tag expression** rather than by a direct
table grant. The tag expression `classification=internal AND
department=public_safety` matches our table — but only on the columns
tagged `internal`. The two PII columns (tagged `confidential`) are excluded.

```bash
assume "$ARN_TAGGED"
# Run in Athena: SELECT * FROM chicago_violations.violations LIMIT 5;
# Expected: 7 columns (no owner_email, owner_phone), all 25 rows visible
unassume
```

### #2: Column-level filter — `NoPiiAnalystRole`

```bash
assume "$ARN_NOPII"
# Run in Athena: SELECT * FROM chicago_violations.violations LIMIT 5;
# Expected: 7 columns (no owner_email, owner_phone), all 25 rows visible
unassume
```

### #3: Row-level filter — `SouthDistrictRole`

```bash
assume "$ARN_SOUTH"
# Run in Athena: SELECT DISTINCT violation_district FROM chicago_violations.violations;
# Expected: only 'SOUTH'  (5 rows total, ~5 districts collapsed to 1)
unassume
```

### #4: Cross-account share

In the **consumer** account, after accepting the RAM invitation:

```bash
# Create a resource link to the shared database
aws glue create-database --database-input "{
  \"Name\": \"chicago_violations_link\",
  \"TargetDatabase\": {
    \"CatalogId\": \"<PRODUCER_ACCOUNT_ID>\",
    \"DatabaseName\": \"chicago_violations\"
  }
}"

# Grant a local IAM role SELECT on the resource link
aws lakeformation grant-permissions --principal DataLakePrincipalIdentifier=<consumer-role-arn> \
  --resource '{"Database":{"Name":"chicago_violations_link"}}' \
  --permissions DESCRIBE
```

## Inspecting the LF state

```bash
# List all LF-Tags in the catalog
aws lakeformation list-lf-tags

# Show columns and their classification tag
aws lakeformation get-resource-lf-tags --resource '{
  "TableWithColumns": {
    "DatabaseName": "chicago_violations",
    "Name": "violations",
    "ColumnWildcard": {}
  }
}' --show-assigned-lf-tags

# List data filters
aws lakeformation list-data-cells-filter --table '{
  "DatabaseName": "chicago_violations",
  "Name": "violations"
}'

# List grants on each role
for arn in "$ARN_TAGGED" "$ARN_NOPII" "$ARN_SOUTH"; do
  echo "=== $arn ==="
  aws lakeformation list-permissions \
    --principal "DataLakePrincipalIdentifier=$arn" \
    --query 'PrincipalResourcePermissions[].{Resource:Resource,Permissions:Permissions}'
done
```

## How LF-TBAC scales beyond this demo

The two tags here are illustrative; in practice you build a richer ontology:

```
classification = public | internal | confidential | restricted
department     = finance | hr | engineering | operations | public_safety
region         = us | eu | apac
sensitivity    = pii | phi | pci | none
```

Then you grant access by composing tag expressions per role. Adding a new
table only requires tagging it — no per-table grants needed. This is the
core advantage of LF-TBAC over named-resource grants and the reason most
enterprise lakes prefer it.

## Tear Down

Two scripts under `scripts/` clean up everything created by deploying and
cross-account testing. **Order matters when cross-account is involved**: the
consumer must release its accepted RAM shares before the producer's stack
can drop the underlying LF resources cleanly.

### Single-account deploy (no cross-account)

```bash
AWS_PROFILE=<your-profile> AWS_REGION=us-east-1 \
  ./scripts/teardown_producer.sh
```

Removes:
- the manually-created Athena query-results bucket (`lf-bootstrap-athena-results-*`)
- the CloudFormation stack (S3 lake bucket, Glue DB+table, LF tags + tag
  associations, both data filters, all four IAM roles, the seed Lambda, and
  any LF cross-account RAM shares created by the stack)

### Cross-account deploy

Run **consumer first**, then producer:

```bash
# 1. Consumer side — remove resource link, IAM role, results bucket, and
#    disassociate RAM shares received from producer
AWS_PROFILE=default-consumer AWS_REGION=us-east-1 \
  ./scripts/teardown_consumer.sh

# 2. Producer side — destroys the stack
AWS_PROFILE=default AWS_REGION=us-east-1 \
  ./scripts/teardown_producer.sh
```

If the producer-side `cdk destroy` hangs on `ConsumerAccountTableGrant` or
`ConsumerAccountDbGrant`, the consumer hasn't released the share yet —
re-run `teardown_consumer.sh`, wait 30 seconds, then retry the producer
script. As a last resort, the producer's grants can be force-revoked:

```bash
ACCT=$(aws sts get-caller-identity --query Account --output text)
aws lakeformation revoke-permissions \
  --principal DataLakePrincipalIdentifier=<consumer-account-id> \
  --resource "{\"Table\":{\"CatalogId\":\"$ACCT\",\"DatabaseName\":\"chicago_violations\",\"Name\":\"violations\"}}" \
  --permissions SELECT DESCRIBE \
  --permissions-with-grant-option SELECT DESCRIBE
```

### What the scripts do NOT remove

- **LF data lake admin status** on either account — was a prerequisite, not
  something the stack created. Adjust manually via `put-data-lake-settings`
  if you need to revoke.
- **CDK bootstrap stack** (`CDKToolkit`) — leave it; other CDK projects
  share it.
- **Any rows in CloudWatch Logs** generated by the seed Lambda or LF
  query logs — these are tiny and age out, but you can purge them
  manually under `/aws/lambda/<SeedFunction>` if desired.

## Notes and caveats

- The CFN execution role is added as an LF admin so the stack can manage
  tags, filters, and grants. This is required — LF requires admin status to
  perform these operations, even from CloudFormation.
- The 25-row CSV is intentionally tiny so the demo is fast. Replace
  `lambda/seed_data.py` with a larger generator if you want to test
  performance.
- LF data filters and LF-TBAC compose: you can grant a tag-expression
  permission *and* attach a data filter to the same table. This stack keeps
  them on separate roles for clarity.
- Cross-account grants do not auto-accept; the consumer account must accept
  the RAM invitation. The stack does not create the consumer-side resource
  link — that is intentionally left manual so each consumer can name and
  scope their link as they choose.

## Reference

Lake Formation docs:
- [LF-TBAC overview](https://docs.aws.amazon.com/lake-formation/latest/dg/tag-based-access-control.html)
- [Data filters](https://docs.aws.amazon.com/lake-formation/latest/dg/data-filters-about.html)
- [Cross-account sharing](https://docs.aws.amazon.com/lake-formation/latest/dg/cross-account-permissions.html)
