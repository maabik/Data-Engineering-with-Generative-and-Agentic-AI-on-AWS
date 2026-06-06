#!/bin/bash
#
# Configure the IAM side of QuickSight -> Redshift access:
#   1. Ensure the QuickSight service role exists.
#   2. Attach a least-privilege policy that allows GetClusterCredentials for
#      our specific cluster + DB + user.
#   3. Whitelist the AWS-published QuickSight egress CIDR for this region on
#      the cluster's VPC security groups (NEVER 0.0.0.0/0).
#
# Does NOT create the DB user or set its password — that's
# create-quicksight-db-user.sh (which uses Secrets Manager).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws jq
require_identity_vars
require_var AWS_ACCOUNT_ID AWS_REGION REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE \
            QS_DB_USER IAM_POLICY_NAME QUICKSIGHT_SERVICE_ROLE

POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${IAM_POLICY_NAME}"

log "Step 1/4: Ensure IAM policy '$IAM_POLICY_NAME' exists."

POLICY_DOC=$(jq -nc \
    --arg region    "$AWS_REGION" \
    --arg account   "$AWS_ACCOUNT_ID" \
    --arg cluster   "$REDSHIFT_CLUSTER_ID" \
    --arg db        "$REDSHIFT_DATABASE" \
    --arg dbuser    "$QS_DB_USER" '
    {
        Version: "2012-10-17",
        Statement: [
            {
                Effect: "Allow",
                Action: [
                    "redshift:DescribeClusters",
                    "redshift:DescribeClusterSubnetGroups",
                    "redshift:DescribeClusterParameterGroups",
                    "redshift:DescribeClusterSecurityGroups",
                    "redshift:GetClusterCredentials",
                    "redshift:JoinGroup"
                ],
                Resource: [
                    ("arn:aws:redshift:" + $region + ":" + $account + ":cluster:" + $cluster),
                    ("arn:aws:redshift:" + $region + ":" + $account + ":dbname:" + $cluster + "/" + $db),
                    ("arn:aws:redshift:" + $region + ":" + $account + ":dbuser:" + $cluster + "/" + $dbuser)
                ]
            }
        ]
    }')

if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    log "Policy exists — creating new default version."
    # Trim non-default versions if at the 5-version limit.
    OLD_VERSIONS=$(aws iam list-policy-versions \
        --policy-arn "$POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' \
        --output text)
    for v in $OLD_VERSIONS; do
        aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v"
    done
    aws iam create-policy-version \
        --policy-arn "$POLICY_ARN" \
        --policy-document "$POLICY_DOC" \
        --set-as-default >/dev/null
else
    aws iam create-policy \
        --policy-name "$IAM_POLICY_NAME" \
        --policy-document "$POLICY_DOC" \
        --description "Least-privilege QuickSight access to the $REDSHIFT_CLUSTER_ID Redshift cluster" >/dev/null
fi

log "Step 2/4: Ensure QuickSight service role '$QUICKSIGHT_SERVICE_ROLE' exists."

if ! aws iam get-role --role-name "$QUICKSIGHT_SERVICE_ROLE" >/dev/null 2>&1; then
    TRUST_POLICY=$(jq -nc '
        {
            Version: "2012-10-17",
            Statement: [{
                Effect: "Allow",
                Principal: {Service: "quicksight.amazonaws.com"},
                Action: "sts:AssumeRole"
            }]
        }')
    aws iam create-role \
        --role-name "$QUICKSIGHT_SERVICE_ROLE" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --description "Service role for Amazon QuickSight" >/dev/null
fi

# Attach if not already attached. attach-role-policy is naturally idempotent.
aws iam attach-role-policy \
    --role-name "$QUICKSIGHT_SERVICE_ROLE" \
    --policy-arn "$POLICY_ARN" >/dev/null

log "Step 3/4: Update QuickSight account-level notification email."
aws quicksight update-account-settings \
    --aws-account-id "$AWS_ACCOUNT_ID" \
    --default-namespace "${QUICKSIGHT_NAMESPACE:-default}" \
    --notification-email "$NOTIFICATION_EMAIL" \
    --region "$AWS_REGION" >/dev/null

log "Step 4/4: Whitelist QuickSight egress CIDRs on the cluster's VPC security groups."

# Discover the VPC security groups attached to the cluster (NOT the legacy
# EC2-classic cluster-security-group used by the old script).
# Use word-splitting via $(...) instead of `mapfile` so this works on
# macOS's default bash 3.2.
VPC_SG_IDS=$(aws redshift describe-clusters \
    --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
    --region "$AWS_REGION" \
    --query 'Clusters[0].VpcSecurityGroups[?Status==`active`].VpcSecurityGroupId' \
    --output text)

if [[ -z "$VPC_SG_IDS" ]]; then
    fatal "Cluster '$REDSHIFT_CLUSTER_ID' has no active VPC security groups. Refusing to fall back to legacy EC2-classic 'default' SG with 0.0.0.0/0."
fi

# Get the published QuickSight egress CIDRs for this region.
QS_CIDRS=$(quicksight_cidrs_for_region)

for sg in $VPC_SG_IDS; do
    for cidr in $QS_CIDRS; do
        log "  Authorizing $cidr -> $sg:5439"
        # authorize-security-group-ingress fails if the rule already exists.
        # Convert that specific failure into a no-op; rethrow anything else.
        if ! out=$(aws ec2 authorize-security-group-ingress \
                --group-id "$sg" \
                --protocol tcp --port 5439 \
                --cidr "$cidr" 2>&1); then
            if [[ "$out" == *"InvalidPermission.Duplicate"* ]]; then
                log "    rule already present"
            else
                printf '%s\n' "$out" >&2
                fatal "Failed to authorize ingress on $sg"
            fi
        fi
    done
done

log "QuickSight -> Redshift IAM and networking configuration complete."
log "Next: ./bootstrap-secret.sh (if not yet) and ./create-quicksight-db-user.sh"
