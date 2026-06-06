#!/bin/bash
#
# Grant SELECT permissions to an existing QuickSight DB user across the demo
# schemas, and create the comprehensive sales view used by the dataset/topic.
# Requires that the user already exists (run create-quicksight-db-user.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.env
source "${SCRIPT_DIR}/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

require_cmd aws
require_var REDSHIFT_CLUSTER_ID REDSHIFT_DATABASE QS_DB_USER

log "Granting permissions to existing user '$QS_DB_USER'."

# Schema usage
redshift_exec_sql "GRANT USAGE ON SCHEMA public TO ${QS_DB_USER};" \
    "Grant USAGE on public" >/dev/null
redshift_exec_sql "GRANT USAGE ON SCHEMA sales TO ${QS_DB_USER};" \
    "Grant USAGE on sales" >/dev/null

# Bulk SELECT
redshift_exec_sql "GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${QS_DB_USER};" \
    "Grant SELECT on public.*" >/dev/null
redshift_exec_sql "GRANT SELECT ON ALL TABLES IN SCHEMA sales TO ${QS_DB_USER};" \
    "Grant SELECT on sales.*" >/dev/null
redshift_exec_sql "GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO ${QS_DB_USER};" \
    "Grant SELECT on information_schema.*" >/dev/null

# Specific pre-built views
for view in sales_comprehensive sales_simple v_monthly_sales v_product_performance; do
    redshift_exec_sql "GRANT SELECT ON sales.${view} TO ${QS_DB_USER};" \
        "Grant SELECT on sales.${view}" >/dev/null || warn "sales.${view} may not exist yet"
done

# Note: sales.sales_comprehensive (queried by 02-create-dataset.sh)
# is a pre-existing view in this cluster; we only need to GRANT on it,
# which is handled in the loop above. The previous "build a synthetic
# quicksight_sales_view here" block was removed — its schema assumptions
# (time_key, sales_territory_key) didn't match this cluster's fact_sales.

log "Permissions complete."
log "Connection details:"
log "  Host:     $(get_redshift_endpoint)"
log "  Port:     5439"
log "  Database: $REDSHIFT_DATABASE"
log "  Username: $QS_DB_USER  (password lives in Secrets Manager: $QS_DB_SECRET_NAME)"
