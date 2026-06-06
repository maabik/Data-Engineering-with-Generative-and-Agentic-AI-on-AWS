#!/bin/bash
# Quick read-only inspection of the Redshift schemas/tables.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws
require_var REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE REDSHIFT_ADMIN_USER

show_query() {
    local sql="$1"
    local description="$2"
    echo
    echo "=== $description ==="
    local stmt_id
    stmt_id=$(redshift_exec_sql "$sql" "$description" 2>/dev/null) \
        || { warn "Query failed: $description"; return 0; }
    aws redshift-data get-statement-result --id "$stmt_id" --query 'Records' --output table
}

show_query "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name;" \
    "Available schemas"

show_query "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;" \
    "Tables in public"

show_query "SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY table_schema, table_name;" \
    "All user tables"

show_query "SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';" \
    "Count of tables in public"
