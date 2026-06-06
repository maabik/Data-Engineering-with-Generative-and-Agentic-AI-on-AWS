"""Lake Formation collector — admins, permissions, LF-Tags, registered resources."""
from __future__ import annotations

from typing import Any

from ._util import make_client, paginate, safe_call


def collect_lakeformation(region: str, profile: str | None = None) -> dict[str, Any]:
    lf = make_client("lakeformation", region, profile)

    settings = safe_call(lf.get_data_lake_settings, default={"DataLakeSettings": {}})
    admins = (settings or {}).get("DataLakeSettings", {}).get("DataLakeAdmins", [])
    create_db_default = (settings or {}).get("DataLakeSettings", {}).get(
        "CreateDatabaseDefaultPermissions", []
    )
    create_table_default = (settings or {}).get("DataLakeSettings", {}).get(
        "CreateTableDefaultPermissions", []
    )

    permissions = paginate(lf, "list_permissions", "PrincipalResourcePermissions")
    lf_tags = paginate(lf, "list_lf_tags", "LFTags")
    resources = paginate(lf, "list_resources", "ResourceInfoList")

    # Permissions breakdown
    table_perms = sum(1 for p in permissions if "Table" in (p.get("Resource") or {}))
    db_perms = sum(1 for p in permissions if "Database" in (p.get("Resource") or {}))
    column_perms = sum(1 for p in permissions if (p.get("Resource") or {}).get("TableWithColumns"))
    lftag_perms = sum(1 for p in permissions if (p.get("Resource") or {}).get("LFTagPolicy") or (p.get("Resource") or {}).get("LFTag"))

    # IAM_ALLOWED_PRINCIPALS in default permissions = pre-LF "passthrough" mode
    iam_passthrough_default = any(
        "IAM_ALLOWED_PRINCIPALS" in (g.get("Principal", {}) or {}).get("DataLakePrincipalIdentifier", "")
        for g in (create_db_default + create_table_default)
    )

    return {
        "admins": [a.get("DataLakePrincipalIdentifier") for a in admins],
        "admin_count": len(admins),
        "permission_count": len(permissions),
        "table_permission_count": table_perms,
        "database_permission_count": db_perms,
        "column_permission_count": column_perms,
        "lftag_permission_count": lftag_perms,
        "lf_tag_count": len(lf_tags),
        "lf_tags": [t.get("TagKey") for t in lf_tags],
        "registered_resource_count": len(resources),
        "iam_passthrough_default": iam_passthrough_default,
    }
