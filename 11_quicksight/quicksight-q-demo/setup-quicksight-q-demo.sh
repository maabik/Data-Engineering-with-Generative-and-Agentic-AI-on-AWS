#!/bin/bash
#
# QuickSight Q Demo — top-level orchestrator.
#
# Required env vars (see config.env for the full list):
#   QS_USER_ARN        - QuickSight user ARN that should own the demo
#   NOTIFICATION_EMAIL - Email for QuickSight account-level notifications
#
# Usage:
#   QS_USER_ARN=arn:aws:quicksight:us-east-1:123456789012:user/default/alice \
#   NOTIFICATION_EMAIL=alice@example.com \
#   ./setup-quicksight-q-demo.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_identity_vars

cat <<EOF
==========================================
QuickSight Q Demo Setup
==========================================
Account:        $AWS_ACCOUNT_ID
Region:         $AWS_REGION
Cluster:        $REDSHIFT_CLUSTER_ID
QS User:        $QS_USER_ARN
Notify Email:   $NOTIFICATION_EMAIL
DB Secret:      $QS_DB_SECRET_NAME

Prerequisites:
- AWS CLI configured with appropriate permissions
- Redshift cluster '$REDSHIFT_CLUSTER_ID' running with sales data
- QuickSight Enterprise subscription
EOF

read -r -p "Proceed? (y/N): " reply
[[ "$reply" =~ ^[Yy]$ ]] || { log "Cancelled."; exit 0; }

log "Step 1/6: bootstrap-secret.sh — generate / rotate the DB password secret"
"${SCRIPT_DIR}/bootstrap-secret.sh"

log "Step 2/6: setup-quicksight-redshift-permissions.sh — IAM + VPC SG"
"${SCRIPT_DIR}/setup-quicksight-redshift-permissions.sh"

log "Step 3/7: create-quicksight-db-user.sh — Redshift user + password sync"
"${SCRIPT_DIR}/create-quicksight-db-user.sh"

log "Step 4/7: grant-quicksight-permissions.sh — schema GRANTs + view"
"${SCRIPT_DIR}/grant-quicksight-permissions.sh"

log "Step 5/7: 01-create-datasource.sh"
"${SCRIPT_DIR}/01-create-datasource.sh"

log "Step 6/7: 02-create-dataset.sh"
"${SCRIPT_DIR}/02-create-dataset.sh"

log "Step 7/7: 03-create-topic.sh"
"${SCRIPT_DIR}/03-create-topic.sh"

log "Setup complete."
"${SCRIPT_DIR}/setup-summary.sh"
