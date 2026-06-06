# Shared helpers for the QuickSight Q demo scripts.
#
# Usage from another script:
#   set -euo pipefail
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "${SCRIPT_DIR}/config.env"
#   source "${SCRIPT_DIR}/lib/common.sh"
#
# All functions below are guarded so that this file can be sourced multiple times.

if [[ -n "${_QS_DEMO_COMMON_SH_LOADED:-}" ]]; then
    return 0
fi
_QS_DEMO_COMMON_SH_LOADED=1

log()   { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
warn()  { printf '[%s] WARN: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
fatal() { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || fatal "Required command not found: $cmd"
    done
}

require_var() {
    local name
    for name in "$@"; do
        if [[ -z "${!name:-}" ]]; then
            fatal "Required variable '$name' is not set. Export it or set it in config.env."
        fi
    done
}

require_identity_vars() {
    require_var QS_USER_ARN NOTIFICATION_EMAIL
    if [[ "$QS_USER_ARN" != arn:aws:quicksight:* ]]; then
        fatal "QS_USER_ARN must look like arn:aws:quicksight:<region>:<acct>:user/<ns>/<name>; got: $QS_USER_ARN"
    fi
    if [[ "$NOTIFICATION_EMAIL" != *@* ]]; then
        fatal "NOTIFICATION_EMAIL must be a valid email; got: $NOTIFICATION_EMAIL"
    fi
}

# Echo the discovered Redshift cluster endpoint (no hardcoded subdomain).
get_redshift_endpoint() {
    require_var REDSHIFT_CLUSTER_ID AWS_REGION
    aws redshift describe-clusters \
        --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
        --region "$AWS_REGION" \
        --query 'Clusters[0].Endpoint.Address' \
        --output text
}

# Fetch the QuickSight DB user's password from Secrets Manager.
# The secret value MUST be a JSON object: {"username":"...", "password":"..."}.
get_qs_db_password() {
    require_var QS_DB_SECRET_NAME AWS_REGION
    require_cmd jq
    local secret_json
    secret_json=$(aws secretsmanager get-secret-value \
        --secret-id "$QS_DB_SECRET_NAME" \
        --region "$AWS_REGION" \
        --query SecretString \
        --output text 2>/dev/null) \
        || fatal "Could not read secret '$QS_DB_SECRET_NAME'. Run ./bootstrap-secret.sh first."
    printf '%s' "$secret_json" | jq -r '.password // empty' \
        | { read -r pw; [[ -n "$pw" ]] || fatal "Secret '$QS_DB_SECRET_NAME' missing 'password' key."; printf '%s' "$pw"; }
}

# Echo the AWS-published QuickSight egress CIDR list for the configured region,
# one CIDR per line. Used to whitelist QuickSight specifically — never 0.0.0.0/0.
# Reference: https://docs.aws.amazon.com/quicksight/latest/user/regions-qs.html
quicksight_cidrs_for_region() {
    require_var AWS_REGION
    case "$AWS_REGION" in
        us-east-1)      printf '52.23.63.224/27\n' ;;
        us-east-2)      printf '52.15.247.160/27\n' ;;
        us-west-2)      printf '54.70.204.128/27\n' ;;
        eu-west-1)      printf '52.210.255.224/27\n' ;;
        eu-west-2)      printf '35.177.218.0/27\n' ;;
        eu-central-1)   printf '35.158.127.192/27\n' ;;
        ap-northeast-1) printf '13.113.244.32/27\n' ;;
        ap-northeast-2) printf '13.124.145.32/27\n' ;;
        ap-southeast-1) printf '13.229.254.0/27\n' ;;
        ap-southeast-2) printf '54.153.249.96/27\n' ;;
        ap-south-1)     printf '52.66.193.64/27\n' ;;
        sa-east-1)      printf '54.232.40.32/27\n' ;;
        ca-central-1)   printf '52.60.198.0/27\n' ;;
        *) fatal "No published QuickSight CIDR mapping for region '$AWS_REGION'. Look it up at https://docs.aws.amazon.com/quicksight/latest/user/regions-qs.html and extend lib/common.sh::quicksight_cidrs_for_region." ;;
    esac
}

# Run a Redshift SQL statement via the Data API and wait for completion.
#  - Uses IAM-based auth via --db-user (no plaintext password on the wire).
#  - Returns 0 on FINISHED, 1 on FAILED.
#  - Echoes the statement Id on stdout.
redshift_exec_sql() {
    local sql="$1"
    local description="${2:-(no description)}"
    local db_user="${3:-${REDSHIFT_ADMIN_USER:-admin}}"
    require_var REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE
    log "Redshift SQL: $description"
    local stmt_id
    stmt_id=$(aws redshift-data execute-statement \
        --cluster-identifier "$REDSHIFT_CLUSTER_ID" \
        --database "$REDSHIFT_DATABASE" \
        --db-user "$db_user" \
        --sql "$sql" \
        --query 'Id' \
        --output text)
    while :; do
        local status
        status=$(aws redshift-data describe-statement \
            --id "$stmt_id" \
            --query 'Status' \
            --output text)
        case "$status" in
            FINISHED) log "  done."; printf '%s' "$stmt_id"; return 0 ;;
            FAILED|ABORTED)
                aws redshift-data describe-statement --id "$stmt_id" --query 'Error' --output text >&2
                return 1 ;;
            *) sleep 2 ;;
        esac
    done
}
