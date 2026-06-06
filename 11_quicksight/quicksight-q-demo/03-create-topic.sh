#!/bin/bash
#
# Create or update the QuickSight Q topic that exposes the sales dataset for
# natural-language queries. Idempotent: describe-topic check + update path.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_identity_vars
require_var AWS_ACCOUNT_ID AWS_REGION DATASET_ID DATASET_NAME TOPIC_ID TOPIC_NAME

log "Configuring Q topic '$TOPIC_ID' over dataset '$DATASET_ID'"

TOPIC_ACTIONS_CSV="quicksight:DescribeTopic,quicksight:DescribeTopicRefresh,quicksight:ListTopicRefreshSchedules,quicksight:DescribeTopicRefreshSchedule,quicksight:DeleteTopic,quicksight:UpdateTopic,quicksight:CreateTopicRefreshSchedule,quicksight:DeleteTopicRefreshSchedule,quicksight:UpdateTopicRefreshSchedule,quicksight:DescribeTopicPermissions,quicksight:UpdateTopicPermissions"

TOPIC_BODY=$(jq -nc \
    --arg name        "$TOPIC_NAME" \
    --arg description "Sales data topic for natural language queries" \
    --arg datasetArn  "arn:aws:quicksight:${AWS_REGION}:${AWS_ACCOUNT_ID}:dataset/${DATASET_ID}" \
    --arg datasetName "$DATASET_NAME" '
    {
      Name: $name,
      Description: $description,
      DataSets: [{
        DatasetArn: $datasetArn,
        DatasetName: $datasetName,
        DatasetDescription: "Comprehensive sales data",
        DataAggregation: {
          DatasetRowDateGranularity: "DAY",
          DefaultDateColumnName: "sales_date"
        },
        Columns: [
          {ColumnName:"total_amount",   ColumnFriendlyName:"Sales Amount", ColumnDescription:"Total sales revenue in dollars",
           ColumnDataRole:"MEASURE",   SemanticType:{TypeName:"CURRENCY"}, Aggregation:"SUM", IsIncludedInTopic:true},
          {ColumnName:"quantity",       ColumnFriendlyName:"Quantity",     ColumnDescription:"Number of units sold",
           ColumnDataRole:"MEASURE",   Aggregation:"SUM", IsIncludedInTopic:true},
          {ColumnName:"profit",         ColumnFriendlyName:"Profit",       ColumnDescription:"Profit amount in dollars",
           ColumnDataRole:"MEASURE",   SemanticType:{TypeName:"CURRENCY"}, Aggregation:"SUM", IsIncludedInTopic:true},
          {ColumnName:"unit_price",     ColumnFriendlyName:"Unit Price",   ColumnDescription:"Price per unit",
           ColumnDataRole:"MEASURE",   SemanticType:{TypeName:"CURRENCY"}, Aggregation:"AVERAGE", IsIncludedInTopic:true},
          {ColumnName:"discount",       ColumnFriendlyName:"Discount",     ColumnDescription:"Discount amount",
           ColumnDataRole:"MEASURE",   SemanticType:{TypeName:"CURRENCY"}, Aggregation:"SUM", IsIncludedInTopic:true},
          {ColumnName:"product_name",   ColumnFriendlyName:"Product",      ColumnDescription:"Product name",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"category",       ColumnFriendlyName:"Category",     ColumnDescription:"Product category",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"subcategory",    ColumnFriendlyName:"Subcategory",  ColumnDescription:"Product subcategory",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"region",         ColumnFriendlyName:"Region",       ColumnDescription:"Sales region",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"country",        ColumnFriendlyName:"Country",      ColumnDescription:"Country",
           ColumnDataRole:"DIMENSION", SemanticType:{TypeName:"GEOGRAPHY", SubTypeName:"COUNTRY"}, IsIncludedInTopic:true},
          {ColumnName:"city",           ColumnFriendlyName:"City",         ColumnDescription:"City",
           ColumnDataRole:"DIMENSION", SemanticType:{TypeName:"GEOGRAPHY", SubTypeName:"CITY"}, IsIncludedInTopic:true},
          {ColumnName:"customer_name",  ColumnFriendlyName:"Customer",     ColumnDescription:"Customer account name",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"sales_rep",      ColumnFriendlyName:"Sales Rep",    ColumnDescription:"Sales representative",
           ColumnDataRole:"DIMENSION", IsIncludedInTopic:true},
          {ColumnName:"sales_date",     ColumnFriendlyName:"Sales Date",   ColumnDescription:"Date when sale occurred",
           ColumnDataRole:"DIMENSION", SemanticType:{TypeName:"DATE"}, IsIncludedInTopic:true}
        ]
      }]
    }')

if aws quicksight describe-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "Topic exists — updating."
    aws quicksight update-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --topic "$TOPIC_BODY" \
        --region "$AWS_REGION" >/dev/null
else
    log "Creating new topic."
    aws quicksight create-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --topic "$TOPIC_BODY" \
        --region "$AWS_REGION" >/dev/null
fi

# update-topic does not accept --permissions, and create-topic only grants the
# caller. Always reconcile permissions afterwards so $QS_USER_ARN can see it
# in the console — important when the topic was originally created by a
# different identity (e.g. an earlier deploy from another role-session).
log "Granting $QS_USER_ARN permissions on topic."
aws quicksight update-topic-permissions \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --topic-id "$TOPIC_ID" \
    --region "$AWS_REGION" \
    --grant-permissions "Principal=${QS_USER_ARN},Actions=${TOPIC_ACTIONS_CSV}" \
    >/dev/null

sleep 5
aws quicksight describe-topic \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --topic-id "$TOPIC_ID" \
    --region "$AWS_REGION" \
    --query '{Name:Topic.Name, Description:Topic.Description, DataSetsCount:length(Topic.DataSets)}' \
    --output table

log "Done. Try Q in the QuickSight console: https://${AWS_REGION}.quicksight.aws.amazon.com/"
