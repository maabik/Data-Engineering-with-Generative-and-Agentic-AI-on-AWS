#!/bin/bash
#
# Create the QuickSight dataset that backs the Q topic. Idempotent: deletes
# and recreates if a dataset with the same ID already exists.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_identity_vars
require_var AWS_ACCOUNT_ID AWS_REGION DATA_SOURCE_ID DATASET_ID DATASET_NAME

log "Creating QuickSight dataset '$DATASET_ID' from data source '$DATA_SOURCE_ID'"

if aws quicksight describe-data-set \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "Dataset exists — deleting before recreate."
    aws quicksight delete-data-set \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION" >/dev/null
    sleep 5
fi

# Schema matches sales.sales_comprehensive on the real cluster (verified via
# information_schema.columns). Keep this list in sync with the view if the
# upstream schema changes.
PHYSICAL_TABLE_MAP=$(jq -nc \
    --arg region    "$AWS_REGION" \
    --arg account   "$AWS_ACCOUNT_ID" \
    --arg dsid      "$DATA_SOURCE_ID" '
    {
      salestable: {
        RelationalTable: {
          DataSourceArn: ("arn:aws:quicksight:" + $region + ":" + $account + ":datasource/" + $dsid),
          Catalog: "salesdb",
          Schema: "sales",
          Name: "sales_comprehensive",
          InputColumns: [
            {Name: "sale_id",         Type: "INTEGER"},
            {Name: "order_id",        Type: "STRING"},
            {Name: "product_name",    Type: "STRING"},
            {Name: "category",        Type: "STRING"},
            {Name: "subcategory",     Type: "STRING"},
            {Name: "customer_name",   Type: "STRING"},
            {Name: "customer_id",     Type: "STRING"},
            {Name: "region",          Type: "STRING"},
            {Name: "country",         Type: "STRING"},
            {Name: "sales_rep",       Type: "STRING"},
            {Name: "sales_date",      Type: "DATETIME"},
            {Name: "city",            Type: "STRING"},
            {Name: "quantity",        Type: "INTEGER"},
            {Name: "unit_price",      Type: "DECIMAL"},
            {Name: "total_amount",    Type: "DECIMAL"},
            {Name: "discount",        Type: "DECIMAL"},
            {Name: "profit",          Type: "DECIMAL"}
          ]
        }
      }
    }')

LOGICAL_TABLE_MAP=$(jq -nc '
    {
      saleslogical: {
        Alias: "Sales Data",
        DataTransforms: [{
          CreateColumnsOperation: {
            Columns: [
              {ColumnName: "profit_margin",    ColumnId: "profitmargin",    Expression: "profit / total_amount"},
              {ColumnName: "revenue_per_unit", ColumnId: "revenueperunit",  Expression: "total_amount / quantity"}
            ]
          }
        }],
        Source: {PhysicalTableId: "salestable"}
      }
    }')

PERMISSIONS=$(jq -nc --arg principal "$QS_USER_ARN" '
    [{
        Principal: $principal,
        Actions: [
            "quicksight:DescribeDataSet",
            "quicksight:DescribeDataSetPermissions",
            "quicksight:PassDataSet",
            "quicksight:DescribeIngestion",
            "quicksight:ListIngestions",
            "quicksight:UpdateDataSet",
            "quicksight:DeleteDataSet",
            "quicksight:CreateIngestion",
            "quicksight:CancelIngestion",
            "quicksight:UpdateDataSetPermissions"
        ]
    }]')

aws quicksight create-data-set \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --data-set-id "$DATASET_ID" \
    --name "$DATASET_NAME" \
    --import-mode SPICE \
    --physical-table-map "$PHYSICAL_TABLE_MAP" \
    --logical-table-map "$LOGICAL_TABLE_MAP" \
    --permissions "$PERMISSIONS" \
    --region "$AWS_REGION" >/dev/null

log "Dataset created. Waiting briefly before SPICE ingestion."
sleep 10

INGESTION_ID="ingestion$(date +%s)"
aws quicksight create-ingestion \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --data-set-id "$DATASET_ID" \
    --ingestion-id "$INGESTION_ID" \
    --region "$AWS_REGION" >/dev/null

log "Ingestion started: $INGESTION_ID. Polling..."
sleep 30
for i in $(seq 1 10); do
    STATUS=$(aws quicksight describe-ingestion \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --ingestion-id "$INGESTION_ID" \
        --region "$AWS_REGION" \
        --query 'Ingestion.IngestionStatus' \
        --output text)
    log "  attempt $i/10 — status: $STATUS"
    case "$STATUS" in
        COMPLETED) break ;;
        FAILED)
            aws quicksight describe-ingestion \
                --aws-account-id "$AWS_ACCOUNT_ID" \
                --data-set-id "$DATASET_ID" \
                --ingestion-id "$INGESTION_ID" \
                --region "$AWS_REGION" \
                --query 'ErrorInfo' --output json >&2
            fatal "Ingestion failed."
            ;;
        *) sleep 30 ;;
    esac
done

aws quicksight describe-data-set \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --data-set-id "$DATASET_ID" \
    --region "$AWS_REGION" \
    --query 'DataSet.{Name:Name, ImportMode:ImportMode, ConsumedSpiceCapacityInBytes:ConsumedSpiceCapacityInBytes, LastUpdatedTime:LastUpdatedTime}' \
    --output table

log "Done. Next: ./03-create-topic.sh"
