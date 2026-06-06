#!/bin/bash
# Pre-flight validation for the QuickSight Q demo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_identity_vars
require_var AWS_ACCOUNT_ID AWS_REGION REDSHIFT_CLUSTER_ID

echo "Validating QuickSight Q demo prerequisites..."
echo "  Account: $AWS_ACCOUNT_ID"
echo "  Region:  $AWS_REGION"

aws sts get-caller-identity >/dev/null \
    || fatal "AWS credentials not configured. Run 'aws configure' (or assume a role)."

CLUSTER_STATUS=$(aws redshift describe-clusters \
    --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
    --region "$AWS_REGION" \
    --query 'Clusters[0].ClusterStatus' \
    --output text 2>/dev/null || true)

case "$CLUSTER_STATUS" in
    "")        fatal "Redshift cluster '$REDSHIFT_CLUSTER_ID' not found." ;;
    available) log "Redshift cluster is available." ;;
    *)         warn "Redshift cluster status: $CLUSTER_STATUS (expected 'available')." ;;
esac

# Confirm that the configured QS user actually exists. ARN format is
# arn:aws:quicksight:<region>:<acct>:user/<namespace>/<name>, where <name>
# may itself contain slashes (e.g. role-session names like 'Admin/alice').
_qs_user_after_prefix="${QS_USER_ARN#*:user/}"
QS_USER_NAME="${_qs_user_after_prefix#*/}"
if aws quicksight describe-user \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --namespace "${QUICKSIGHT_NAMESPACE:-default}" \
        --user-name "$QS_USER_NAME" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "QuickSight user '$QS_USER_NAME' exists."
else
    fatal "QuickSight user '$QS_USER_NAME' (from QS_USER_ARN) not found in namespace '${QUICKSIGHT_NAMESPACE:-default}'. Subscribe to QuickSight or register the user first."
fi

# Confirm Secrets Manager secret is bootstrapped.
if aws secretsmanager describe-secret \
        --secret-id "$QS_DB_SECRET_NAME" \
        --region "$AWS_REGION" >/dev/null 2>&1; then
    log "Secret '$QS_DB_SECRET_NAME' is present."
else
    warn "Secret '$QS_DB_SECRET_NAME' not found — run ./bootstrap-secret.sh before continuing."
fi

if command -v python3 >/dev/null 2>&1; then
    log "python3 found (used by generate-story-content.py)."
else
    warn "python3 not found — story generation features will not work."
fi

log "Validation complete. Ready: ./setup-quicksight-q-demo.sh"
