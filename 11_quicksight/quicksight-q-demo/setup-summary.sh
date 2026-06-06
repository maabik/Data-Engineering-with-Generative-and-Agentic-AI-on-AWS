#!/bin/bash
# Print a status summary of the demo's QuickSight + Redshift resources.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_var AWS_ACCOUNT_ID AWS_REGION DATA_SOURCE_ID DATASET_ID TOPIC_ID REDSHIFT_CLUSTER_ID

echo "=========================================="
echo "QuickSight Q Demo — Setup Summary"
echo "Account:  $AWS_ACCOUNT_ID"
echo "Region:   $AWS_REGION"
echo "=========================================="

echo
echo "1. QuickSight subscription:"
aws quicksight describe-account-subscription \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --region "$AWS_REGION" \
    --query '{Edition:AccountInfo.Edition, Status:AccountInfo.AccountSubscriptionStatus}' \
    --output table || warn "describe-account-subscription failed"

echo
echo "2. Data source ($DATA_SOURCE_ID):"
if aws quicksight describe-data-source \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-source-id "$DATA_SOURCE_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    aws quicksight describe-data-source \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-source-id "$DATA_SOURCE_ID" \
        --region "$AWS_REGION" \
        --query 'DataSource.{Name:Name, Type:Type, Status:Status, Host:DataSourceParameters.RedshiftParameters.Host, VpcConnectionArn:VpcConnectionProperties.VpcConnectionArn}' \
        --output table
else
    echo "  (not found)"
fi

echo
echo "3. Dataset ($DATASET_ID):"
if aws quicksight describe-data-set \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    aws quicksight describe-data-set \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION" \
        --query 'DataSet.{Name:Name, ImportMode:ImportMode, LastUpdatedTime:LastUpdatedTime, ConsumedSpiceCapacityInBytes:ConsumedSpiceCapacityInBytes}' \
        --output table

    echo
    echo "4. Latest ingestion:"
    LATEST_INGESTION=$(aws quicksight list-ingestions \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION" \
        --query 'Ingestions[0].IngestionId' \
        --output text)
    if [[ -n "$LATEST_INGESTION" && "$LATEST_INGESTION" != "None" ]]; then
        aws quicksight describe-ingestion \
            --aws-account-id "$AWS_ACCOUNT_ID" \
            --data-set-id "$DATASET_ID" \
            --ingestion-id "$LATEST_INGESTION" \
            --region "$AWS_REGION" \
            --query 'Ingestion.{Status:IngestionStatus, SizeInBytes:IngestionSizeInBytes, RowsIngested:RowInfo.RowsIngested, RowsDropped:RowInfo.RowsDropped}' \
            --output table
    else
        echo "  (no ingestions found)"
    fi
else
    echo "  (not found)"
fi

echo
echo "5. Q topic ($TOPIC_ID):"
if aws quicksight describe-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    aws quicksight describe-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --region "$AWS_REGION" \
        --query '{Name:Topic.Name, Description:Topic.Description, DataSetsCount:length(Topic.DataSets), ColumnsCount:length(Topic.DataSets[0].Columns)}' \
        --output table
else
    echo "  (not found)"
fi

echo
echo "6. Redshift cluster ($REDSHIFT_CLUSTER_ID):"
aws redshift describe-clusters \
    --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
    --region "$AWS_REGION" \
    --query 'Clusters[0].{Status:ClusterStatus, Endpoint:Endpoint.Address, Database:DBName}' \
    --output table || echo "  (not found)"

echo
echo "Open QuickSight: https://${AWS_REGION}.quicksight.aws.amazon.com/"
