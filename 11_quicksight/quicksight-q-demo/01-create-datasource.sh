#!/bin/bash
#
# Create (or update) the QuickSight data source pointing at the Redshift sales
# demo cluster. Pulls the DB password from Secrets Manager and discovers the
# cluster endpoint dynamically — no per-account DNS in source.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_identity_vars
require_var AWS_ACCOUNT_ID AWS_REGION DATA_SOURCE_ID DATA_SOURCE_NAME \
            REDSHIFT_DATABASE QS_DB_USER

CLUSTER_ENDPOINT="$(get_redshift_endpoint)"
[[ -n "$CLUSTER_ENDPOINT" && "$CLUSTER_ENDPOINT" != "None" ]] \
    || fatal "Could not discover endpoint for cluster '$REDSHIFT_CLUSTER_ID'."

QS_DB_PASSWORD="$(get_qs_db_password)"

log "Creating QuickSight data source '$DATA_SOURCE_ID' against $CLUSTER_ENDPOINT"

DATA_SOURCE_PARAMS=$(jq -nc \
    --arg host "$CLUSTER_ENDPOINT" \
    --arg db   "$REDSHIFT_DATABASE" \
    '{RedshiftParameters: {Host: $host, Port: 5439, Database: $db}}')

CREDENTIALS=$(jq -nc \
    --arg u "$QS_DB_USER" \
    --arg p "$QS_DB_PASSWORD" \
    '{CredentialPair: {Username: $u, Password: $p}}')

PERMISSIONS=$(jq -nc --arg principal "$QS_USER_ARN" '
    [{
        Principal: $principal,
        Actions: [
            "quicksight:DescribeDataSource",
            "quicksight:DescribeDataSourcePermissions",
            "quicksight:PassDataSource",
            "quicksight:UpdateDataSource",
            "quicksight:DeleteDataSource",
            "quicksight:UpdateDataSourcePermissions"
        ]
    }]')

# Optional VPC connection (required when cluster is private). Set the env var
# QS_VPC_CONNECTION_ID to the ID of an AVAILABLE QuickSight VPC connection.
# Two AWS CLI invocations follow because --vpc-connection-properties cannot be
# passed as an empty value; bash 3.2 has no clean way to expand "optional flag".
if [[ -n "${QS_VPC_CONNECTION_ID:-}" ]]; then
    VPC_ARN="arn:aws:quicksight:${AWS_REGION}:${AWS_ACCOUNT_ID}:vpcConnection/${QS_VPC_CONNECTION_ID}"
    log "Routing through VPC connection: $VPC_ARN"
    VPC_PROPS=$(jq -nc --arg arn "$VPC_ARN" '{VpcConnectionArn: $arn}')
fi

run_create_or_update() {
    local mode="$1"
    if [[ "$mode" == "update" ]]; then
        if [[ -n "${QS_VPC_CONNECTION_ID:-}" ]]; then
            aws quicksight update-data-source \
                --aws-account-id "$AWS_ACCOUNT_ID" \
                --data-source-id "$DATA_SOURCE_ID" \
                --name "$DATA_SOURCE_NAME" \
                --data-source-parameters "$DATA_SOURCE_PARAMS" \
                --credentials "$CREDENTIALS" \
                --ssl-properties '{"DisableSsl": false}' \
                --vpc-connection-properties "$VPC_PROPS" \
                --region "$AWS_REGION" >/dev/null
        else
            aws quicksight update-data-source \
                --aws-account-id "$AWS_ACCOUNT_ID" \
                --data-source-id "$DATA_SOURCE_ID" \
                --name "$DATA_SOURCE_NAME" \
                --data-source-parameters "$DATA_SOURCE_PARAMS" \
                --credentials "$CREDENTIALS" \
                --ssl-properties '{"DisableSsl": false}' \
                --region "$AWS_REGION" >/dev/null
        fi
    else
        if [[ -n "${QS_VPC_CONNECTION_ID:-}" ]]; then
            aws quicksight create-data-source \
                --aws-account-id "$AWS_ACCOUNT_ID" \
                --data-source-id "$DATA_SOURCE_ID" \
                --name "$DATA_SOURCE_NAME" \
                --type REDSHIFT \
                --data-source-parameters "$DATA_SOURCE_PARAMS" \
                --credentials "$CREDENTIALS" \
                --ssl-properties '{"DisableSsl": false}' \
                --permissions "$PERMISSIONS" \
                --vpc-connection-properties "$VPC_PROPS" \
                --region "$AWS_REGION" >/dev/null
        else
            aws quicksight create-data-source \
                --aws-account-id "$AWS_ACCOUNT_ID" \
                --data-source-id "$DATA_SOURCE_ID" \
                --name "$DATA_SOURCE_NAME" \
                --type REDSHIFT \
                --data-source-parameters "$DATA_SOURCE_PARAMS" \
                --credentials "$CREDENTIALS" \
                --ssl-properties '{"DisableSsl": false}' \
                --permissions "$PERMISSIONS" \
                --region "$AWS_REGION" >/dev/null
        fi
    fi
}

if aws quicksight describe-data-source \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-source-id "$DATA_SOURCE_ID" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "Data source exists — updating."
    run_create_or_update update
else
    log "Creating new data source."
    run_create_or_update create
fi

# Don't leave the password in process memory longer than necessary.
unset QS_DB_PASSWORD CREDENTIALS

log "Verifying data source state."
aws quicksight describe-data-source \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --data-source-id "$DATA_SOURCE_ID" \
    --region "$AWS_REGION" \
    --query 'DataSource.{Name:Name, Type:Type, Status:Status, Host:DataSourceParameters.RedshiftParameters.Host, Database:DataSourceParameters.RedshiftParameters.Database, VpcConnectionArn:VpcConnectionProperties.VpcConnectionArn}' \
    --output table

log "Done. Next: ./02-create-dataset.sh"
