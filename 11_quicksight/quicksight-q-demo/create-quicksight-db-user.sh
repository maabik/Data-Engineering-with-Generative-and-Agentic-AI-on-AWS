#!/bin/bash
#
# Create (or update) the QuickSight DB user in Redshift using a password
# fetched from AWS Secrets Manager. Idempotent: ALTER USER ... PASSWORD on
# re-run so that rotating the secret + re-running this script keeps Redshift
# in sync.
#
# Uses the Redshift Data API; admin DB user is taken from REDSHIFT_ADMIN_USER
# in config.env (no admin password needed — Data API uses caller IAM creds).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_var REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE REDSHIFT_ADMIN_USER QS_DB_USER

QS_DB_PASSWORD="$(get_qs_db_password)"

# Sanity: refuse to embed a password containing single quotes — would break the
# SQL. bootstrap-secret.sh already strips these, but defense in depth.
if [[ "$QS_DB_PASSWORD" == *"'"* ]]; then
    fatal "Password from secret contains a single quote; rotate via bootstrap-secret.sh."
fi

log "Ensuring Redshift user '$QS_DB_USER' exists with current password."

# Detect existence via pg_user lookup, then CREATE or ALTER accordingly.
USER_EXISTS_SQL="SELECT 1 FROM pg_user WHERE usename = '${QS_DB_USER}';"
STMT_ID=$(redshift_exec_sql "$USER_EXISTS_SQL" "Check if $QS_DB_USER exists")
RESULT=$(aws redshift-data get-statement-result --id "$STMT_ID" --query 'Records' --output json)
USER_EXISTS=$(jq 'length > 0' <<<"$RESULT")

# Build the CREATE / ALTER statement with the password as a single-quoted
# literal. Single quotes already rejected above.
if [[ "$USER_EXISTS" == "true" ]]; then
    log "User already exists — ALTERing password."
    USER_SQL="ALTER USER ${QS_DB_USER} PASSWORD '${QS_DB_PASSWORD}';"
else
    log "Creating user."
    USER_SQL="CREATE USER ${QS_DB_USER} PASSWORD '${QS_DB_PASSWORD}';"
fi
redshift_exec_sql "$USER_SQL" "Set password on $QS_DB_USER" >/dev/null

unset QS_DB_PASSWORD USER_SQL

# Grant minimum required privileges. Schema-specific grants and the
# comprehensive view (sales.quicksight_sales_view) live in
# grant-quicksight-permissions.sh, which is run as a separate step.
redshift_exec_sql \
    "GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO ${QS_DB_USER};" \
    "Grant SELECT on information_schema.*" >/dev/null

log "Done. The QuickSight user '$QS_DB_USER' is configured."
log "Connection details (password lives in Secrets Manager: $QS_DB_SECRET_NAME):"
log "  Host:     $(get_redshift_endpoint)"
log "  Port:     5439"
log "  Database: $REDSHIFT_DATABASE"
log "  Username: $QS_DB_USER"
