#!/bin/bash
#
# Tear down everything created by the QuickSight Q demo. Each step is
# best-effort: a missing resource is logged and skipped, but unexpected
# errors are surfaced.
#
# Does NOT delete the underlying Redshift cluster, the source schema/data,
# the staging S3 bucket from 10_datamart, or the cluster admin secret —
# only the resources this demo creates.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws
require_var AWS_ACCOUNT_ID AWS_REGION DATA_SOURCE_ID DATASET_ID TOPIC_ID \
            DASHBOARD_ID IAM_POLICY_NAME QUICKSIGHT_SERVICE_ROLE \
            QS_DB_SECRET_NAME QS_DB_USER REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE \
            REDSHIFT_ADMIN_USER QS_VPC_CONNECTION_SG_NAME \
            QS_VPC_CONNECTION_ROLE_NAME QS_VPC_CONNECTION_ROLE_INLINE_POLICY

POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${IAM_POLICY_NAME}"

# Wraps a command so we log "not found" gracefully and surface real errors.
run_step() {
    local description="$1"; shift
    log "$description"
    if out=$("$@" 2>&1); then
        return 0
    fi
    case "$out" in
        *NotFound*|*"does not exist"*|*"could not be found"*|*ResourceNotFoundException*|*"NoSuchEntity"*)
            log "  (already absent — skipping)"
            return 0
            ;;
        *)
            warn "  failed: $out"
            return 0  # don't abort cleanup mid-flight
            ;;
    esac
}

log "QuickSight Q Demo cleanup — starting"

run_step "Delete dashboard $DASHBOARD_ID" \
    aws quicksight delete-dashboard \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --dashboard-id "$DASHBOARD_ID" \
        --region "$AWS_REGION"

run_step "Delete Q topic $TOPIC_ID" \
    aws quicksight delete-topic \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --topic-id "$TOPIC_ID" \
        --region "$AWS_REGION"

run_step "Delete dataset $DATASET_ID" \
    aws quicksight delete-data-set \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-set-id "$DATASET_ID" \
        --region "$AWS_REGION"

run_step "Delete data source $DATA_SOURCE_ID" \
    aws quicksight delete-data-source \
        --aws-account-id "$AWS_ACCOUNT_ID" \
        --data-source-id "$DATA_SOURCE_ID" \
        --region "$AWS_REGION"

# QuickSight VPC connection (only if configured for the demo).
if [[ -n "${QS_VPC_CONNECTION_ID:-}" ]]; then
    run_step "Delete QuickSight VPC connection $QS_VPC_CONNECTION_ID" \
        aws quicksight delete-vpc-connection \
            --aws-account-id "$AWS_ACCOUNT_ID" \
            --vpc-connection-id "$QS_VPC_CONNECTION_ID" \
            --region "$AWS_REGION"

    # delete-vpc-connection is async; wait briefly so the SG release lands
    # before we try to delete the SG below.
    log "  waiting up to 90s for VPC connection to release ENIs"
    for _ in $(seq 1 30); do
        status=$(aws quicksight describe-vpc-connection \
            --aws-account-id "$AWS_ACCOUNT_ID" \
            --vpc-connection-id "$QS_VPC_CONNECTION_ID" \
            --region "$AWS_REGION" \
            --query 'VPCConnection.AvailabilityStatus' \
            --output text 2>/dev/null) || status="GONE"
        [[ "$status" == "GONE" || "$status" == "DELETED" ]] && break
        sleep 3
    done
fi

# Dedicated SG for the QS VPC connection ENIs.
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=${QS_VPC_CONNECTION_SG_NAME}" \
    --region "$AWS_REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)

if [[ -n "$SG_ID" && "$SG_ID" != "None" ]]; then
    # Best-effort: revoke the ingress rule we added on the cluster SG that
    # references this SG, so it can be deleted cleanly.
    CLUSTER_SG_IDS=$(aws redshift describe-clusters \
        --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
        --region "$AWS_REGION" \
        --query 'Clusters[0].VpcSecurityGroups[*].VpcSecurityGroupId' \
        --output text 2>/dev/null || true)
    for csg in $CLUSTER_SG_IDS; do
        run_step "Revoke ingress 5439 on $csg from $SG_ID" \
            aws ec2 revoke-security-group-ingress \
                --group-id "$csg" \
                --protocol tcp --port 5439 \
                --source-group "$SG_ID" \
                --region "$AWS_REGION"
    done

    run_step "Delete SG $SG_ID ($QS_VPC_CONNECTION_SG_NAME)" \
        aws ec2 delete-security-group \
            --group-id "$SG_ID" \
            --region "$AWS_REGION"
else
    log "Security group $QS_VPC_CONNECTION_SG_NAME not found — skipping"
fi

# IAM role used by the QS VPC connection.
if aws iam get-role --role-name "$QS_VPC_CONNECTION_ROLE_NAME" >/dev/null 2>&1; then
    run_step "Delete inline policy $QS_VPC_CONNECTION_ROLE_INLINE_POLICY on $QS_VPC_CONNECTION_ROLE_NAME" \
        aws iam delete-role-policy \
            --role-name "$QS_VPC_CONNECTION_ROLE_NAME" \
            --policy-name "$QS_VPC_CONNECTION_ROLE_INLINE_POLICY"

    # Also detach any managed policies that may have been attached.
    ATTACHED=$(aws iam list-attached-role-policies \
        --role-name "$QS_VPC_CONNECTION_ROLE_NAME" \
        --query 'AttachedPolicies[*].PolicyArn' \
        --output text 2>/dev/null || true)
    for arn in $ATTACHED; do
        run_step "Detach $arn from $QS_VPC_CONNECTION_ROLE_NAME" \
            aws iam detach-role-policy \
                --role-name "$QS_VPC_CONNECTION_ROLE_NAME" \
                --policy-arn "$arn"
    done

    run_step "Delete role $QS_VPC_CONNECTION_ROLE_NAME" \
        aws iam delete-role --role-name "$QS_VPC_CONNECTION_ROLE_NAME"
else
    log "Role $QS_VPC_CONNECTION_ROLE_NAME not found — skipping"
fi

# Detach + delete the IAM policy that grants QuickSight access to Redshift.
# delete-policy refuses if attached or if non-default versions exist.
run_step "Detach $POLICY_ARN from $QUICKSIGHT_SERVICE_ROLE" \
    aws iam detach-role-policy \
        --role-name "$QUICKSIGHT_SERVICE_ROLE" \
        --policy-arn "$POLICY_ARN"

if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    OLD_VERSIONS=$(aws iam list-policy-versions \
        --policy-arn "$POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' \
        --output text)
    for v in $OLD_VERSIONS; do
        run_step "Delete IAM policy version $v" \
            aws iam delete-policy-version \
                --policy-arn "$POLICY_ARN" \
                --version-id "$v"
    done
    run_step "Delete IAM policy $IAM_POLICY_NAME" \
        aws iam delete-policy --policy-arn "$POLICY_ARN"
fi

# Drop the views/user we created in Redshift. Best-effort: cluster may be
# stopped, in which case redshift-data calls will fail and we just warn.
log "Dropping Redshift demo objects (best-effort)"
redshift_exec_sql \
    "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM ${QS_DB_USER}; REVOKE USAGE ON SCHEMA public FROM ${QS_DB_USER}; REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA sales FROM ${QS_DB_USER}; REVOKE USAGE ON SCHEMA sales FROM ${QS_DB_USER}; REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA information_schema FROM ${QS_DB_USER}; DROP USER IF EXISTS ${QS_DB_USER};" \
    "Drop user ${QS_DB_USER}" >/dev/null \
    || warn "could not drop user ${QS_DB_USER} — there may be objects still owned by them"

# Delete the Secrets Manager secret with no recovery window so re-runs work.
run_step "Delete secret $QS_DB_SECRET_NAME" \
    aws secretsmanager delete-secret \
        --secret-id "$QS_DB_SECRET_NAME" \
        --force-delete-without-recovery \
        --region "$AWS_REGION"

log "Cleanup complete."
log "Note: the Redshift cluster '$REDSHIFT_CLUSTER_ID' was NOT deleted."
log "Note: the QuickSight service role '$QUICKSIGHT_SERVICE_ROLE' was NOT deleted (may be shared)."
log "Note: cluster admin secret 'redshift-text-to-sql-credentials' (from 10_datamart) was NOT deleted."
log "Note: any S3 staging bucket (sales-demo-data-*) from 10_datamart was NOT deleted."
