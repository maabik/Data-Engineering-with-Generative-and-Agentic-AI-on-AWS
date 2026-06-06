#!/bin/bash
#
# Bootstrap the Secrets Manager secret that holds the QuickSight DB user's password.
# Generates a strong random password, writes it to Secrets Manager, and prints
# nothing sensitive to stdout. Idempotent: rotates the secret value on re-run.
#
# Required env / config: see config.env (QS_DB_SECRET_NAME, QS_DB_USER, AWS_REGION).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq openssl
require_var QS_DB_SECRET_NAME QS_DB_USER AWS_REGION

# Generate a 32-char password from a URL-safe-ish alphabet. Avoid characters
# that need quoting in SQL or shell.
generate_password() {
    local raw
    raw=$(openssl rand -base64 48 | tr -d '\n+/=' | tr -d "'\"\\\\\$\`")
    printf '%s' "${raw:0:32}"
}

NEW_PASSWORD="$(generate_password)"
SECRET_JSON=$(jq -nc \
    --arg user "$QS_DB_USER" \
    --arg pw   "$NEW_PASSWORD" \
    '{username:$user, password:$pw}')

if aws secretsmanager describe-secret \
        --secret-id "$QS_DB_SECRET_NAME" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "Secret '$QS_DB_SECRET_NAME' exists — rotating value."
    aws secretsmanager put-secret-value \
        --secret-id "$QS_DB_SECRET_NAME" \
        --secret-string "$SECRET_JSON" \
        --region "$AWS_REGION" >/dev/null
else
    log "Creating secret '$QS_DB_SECRET_NAME'."
    aws secretsmanager create-secret \
        --name "$QS_DB_SECRET_NAME" \
        --description "Password for QuickSight DB user '$QS_DB_USER' (Sales Q demo)" \
        --secret-string "$SECRET_JSON" \
        --region "$AWS_REGION" >/dev/null
fi

log "Secret ready. Use lib/common.sh::get_qs_db_password to retrieve it."
log "If the user already exists in Redshift, run create-quicksight-db-user.sh"
log "to ALTER USER ... PASSWORD with the new value."
